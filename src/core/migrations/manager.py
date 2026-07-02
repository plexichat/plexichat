"""
Migration manager for orchestrating database migrations.

This module provides the MigrationManager class which coordinates the
entire migration process including discovery, validation, execution, and tracking.
"""

import logging
import time
from pathlib import Path
from typing import List, Dict, Any, Optional

from src.core.database import cached

from . import validator
from .tracker import MigrationTracker
from .runner import MigrationRunner
from .progress import ProgressBar

logger = logging.getLogger(__name__)


class Migration:
    """Represents a single migration with metadata."""

    def __init__(
        self,
        version: str,
        name: str,
        file_path: str,
        checksum: str = "",
        depends_on: Optional[List[str]] = None,
    ):
        """
        Initialize migration metadata.

        Args:
            version: Version identifier (e.g., '001')
            name: Human-readable name
            file_path: Path to migration file
            checksum: SHA256 checksum of file content
            depends_on: List of migration versions this migration depends on
        """
        self.version = version
        self.name = name
        self.file_path = file_path
        self.checksum = checksum
        self.depends_on = depends_on or []


class MigrationManager:
    """Orchestrates database migrations."""

    def __init__(self, db):
        """
        Initialize the migration manager.

        Args:
            db: Database instance from plexichat.src.core.database
        """
        self.db = db
        self.tracker = MigrationTracker(db)
        self.migrations_dir = Path(__file__).parent / "migrations"

        # Ensure migrations directory exists
        self.migrations_dir.mkdir(parents=True, exist_ok=True)

    def get_pending_migrations(self) -> List[Migration]:
        """
        Get list of pending migrations not yet applied.

        Returns:
            List of Migration objects for migrations not yet applied

        Raises:
            Exception: If migration discovery fails
        """
        self.tracker.ensure_table_exists()

        applied = self.tracker.get_applied_migrations()
        applied_set = set(applied)
        logger.info(f"Currently applied migrations: {applied_set}")

        try:
            migration_files = validator.get_migration_files(str(self.migrations_dir))
            logger.info(
                f"Discovered migration files: {[Path(f[1]).name for f in migration_files]}"
            )
        except NotADirectoryError:
            logger.warning(f"Migrations directory not found: {self.migrations_dir}")
            return []

        pending = []
        for version, file_path in migration_files:
            if version not in applied_set:
                logger.info(f"Found pending migration: {version}")
                # Calculate checksum
                with open(file_path, "rb") as f:
                    content = f.read()
                    checksum = validator.calculate_checksum(content)

                # Extract migration name from filename
                filename = Path(file_path).stem
                name = filename.split("_", 1)[1] if "_" in filename else filename

                # Extract dependencies from docstring
                depends_on = self._extract_dependencies(file_path)

                pending.append(
                    Migration(version, name, file_path, checksum, depends_on)
                )
            else:
                logger.debug(f"Migration {version} already applied")

        return pending

    def _extract_dependencies(self, file_path: str) -> List[str]:
        """
        Extract migration dependencies from file docstring.

        Dependencies can be specified in the docstring as:
        Depends: 001, 005, 010

        Args:
            file_path: Path to migration file

        Returns:
            List of migration version strings this migration depends on
        """
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()

            # Look for "Depends:" in docstring
            for line in content.split("\n"):
                line = line.strip()
                if line.startswith("Depends:"):
                    deps_str = line.split(":", 1)[1].strip()
                    # Parse comma-separated versions
                    deps = [d.strip() for d in deps_str.split(",") if d.strip()]
                    logger.debug(f"Extracted dependencies from {file_path}: {deps}")
                    return deps
        except Exception as e:
            logger.warning(f"Could not extract dependencies from {file_path}: {e}")

        return []

    def validate_dependencies(self, migration: Migration, applied: List[str]) -> bool:
        """
        Validate that all dependencies for a migration are satisfied.

        Args:
            migration: Migration to validate
            applied: List of applied migration versions

        Returns:
            True if all dependencies are satisfied

        Raises:
            ValueError: If dependencies are not satisfied
        """
        if not migration.depends_on:
            return True

        applied_set = set(applied)
        unsatisfied = [dep for dep in migration.depends_on if dep not in applied_set]

        if unsatisfied:
            raise ValueError(
                f"Migration {migration.version} depends on {unsatisfied} which are not applied"
            )

        logger.debug(
            f"Migration {migration.version} dependencies satisfied: {migration.depends_on}"
        )
        return True

    def apply_migration(self, version: str, dry_run: bool = False) -> Dict[str, Any]:
        """
        Apply a single migration.

        Args:
            version: Migration version to apply
            dry_run: If True, execute without committing changes

        Returns:
            Dictionary with migration result

        Raises:
            ValueError: If migration not found or validation fails
            Exception: If migration execution fails
        """
        pending = self.get_pending_migrations()
        migration = None

        for m in pending:
            if m.version == version:
                migration = m
                break

        if migration is None:
            raise ValueError(f"Migration {version} not found in pending migrations")

        # Validate dependencies
        applied = self.tracker.get_applied_migrations()
        self.validate_dependencies(migration, applied)

        # Check if irreversible migration can be run (skip for dry-run)
        if not dry_run:
            import utils.config as config

            delay_days = config.get(
                "database.migrations.irreversible_migration_delay_days", 7
            )
            delay_hours = delay_days * 24

            can_run, reason = self.tracker.can_run_irreversible_migration(
                version, delay_hours
            )
            if not can_run:
                raise ValueError(f"Cannot run migration {version}: {reason}")

        return self._execute_migration(migration, dry_run)

    def apply_all_pending(self, dry_run: bool = False) -> Dict[str, Any]:
        """
        Apply all pending migrations in order.

        Args:
            dry_run: If True, execute without committing changes

        Returns:
            Dictionary with summary of all applied migrations

        Raises:
            Exception: If any migration fails
        """
        # Acquire lock to prevent concurrent migration runs
        if not dry_run:
            if not self.tracker.acquire_lock():
                raise Exception(
                    "Could not acquire migration lock - another migration may be in progress"
                )

        try:
            self.tracker.ensure_table_exists()

            pending = self.get_pending_migrations()

            if not pending:
                logger.info("No pending migrations to apply")
                return {
                    "success": True,
                    "applied_count": 0,
                    "failed_count": 0,
                    "migrations": [],
                    "dry_run": dry_run,
                }

            # Validate migration order
            applied = self.tracker.get_applied_migrations()
            pending_versions = [m.version for m in pending]

            try:
                validator.validate_migration_order(pending_versions, applied)
            except ValueError as e:
                logger.error(f"Migration order validation failed: {str(e)}")
                raise

            results = {
                "success": True,
                "applied_count": 0,
                "failed_count": 0,
                "migrations": [],
                "dry_run": dry_run,
                "total_elapsed_ms": 0,
            }

            total = len(pending)
            batch_start = time.monotonic()
            with ProgressBar("migrations", total=total) as progress:
                for index, migration in enumerate(pending, start=1):
                    migration_start = time.monotonic()
                    try:
                        # Validate dependencies before applying
                        self.validate_dependencies(migration, applied)

                        result = self._execute_migration(migration, dry_run)
                        results["migrations"].append(result)
                        results["applied_count"] += 1

                        # Update applied list for dependency validation of subsequent migrations
                        applied.append(migration.version)

                        elapsed_ms = int((time.monotonic() - migration_start) * 1000)
                        logger.info(
                            f"Migration {migration.version} applied in {elapsed_ms}ms"
                        )
                        progress.set(
                            index,
                            suffix=f"{migration.version} {elapsed_ms}ms",
                        )
                    except Exception as e:
                        results["success"] = False
                        results["failed_count"] += 1
                        error_result = {
                            "version": migration.version,
                            "name": migration.name,
                            "success": False,
                            "error": str(e),
                        }
                        results["migrations"].append(error_result)
                        logger.error(
                            f"Failed to apply migration {migration.version}: {str(e)}"
                        )
                        # Stop on the first failure - this is a fatal state and
                        # continuing would just compound errors. The caller
                        # surfaces the failure so startup can abort.
                        progress.set(index, suffix=f"{migration.version} FAILED")
                        break

            results["total_elapsed_ms"] = int((time.monotonic() - batch_start) * 1000)

            # Invalidate cached migration status after applying all
            if not dry_run and results["applied_count"] > 0:
                from src.core.database import invalidate_pattern

                invalidate_pattern("migration_status:*")

            return results
        finally:
            # Always release the lock
            if not dry_run:
                self.tracker.release_lock()

    def rollback_migration(self, version: str) -> Dict[str, Any]:
        """
        Rollback a specific migration.

        Args:
            version: Migration version to rollback

        Returns:
            Dictionary with rollback result

        Raises:
            ValueError: If migration not found or doesn't have down() function
            Exception: If rollback execution fails
        """
        status = self.tracker.get_migration_status(version)

        if status is None:
            raise ValueError(f"Migration {version} not found in history")

        if status["status"] != "completed":
            raise ValueError(
                f"Cannot rollback migration {version}: status is {status['status']}"
            )

        # Find migration file
        migration_file = None
        try:
            migration_files = validator.get_migration_files(str(self.migrations_dir))
            for v, f in migration_files:
                if v == version:
                    migration_file = f
                    break
        except NotADirectoryError:
            raise ValueError("Migrations directory not found")

        if migration_file is None:
            raise ValueError(f"Migration file for version {version} not found")

        try:
            runner = MigrationRunner(self.db, self.tracker)
            runner.validate_migration_file(migration_file)
            module = runner.load_migration_module(migration_file)
            runner.execute_migration_down(module, version)

            # Mark as rolled back in tracker
            self.tracker.record_rollback(version)

            # Invalidate cached migration status
            from src.core.database import invalidate_pattern

            invalidate_pattern("migration_status:*")

            return {
                "success": True,
                "version": version,
                "message": f"Migration {version} rolled back successfully",
            }

        except Exception as e:
            logger.error(f"Rollback of migration {version} failed: {str(e)}")
            raise

    @cached(ttl=15, prefix="migration_status")
    def get_migration_status(self) -> Dict[str, Any]:
        """
        Get current migration status (cached for 15s to reduce admin dashboard DB load).

        Returns:
            Dictionary with applied, pending, failed counts and lists
        """
        self.tracker.ensure_table_exists()

        applied = self.tracker.get_applied_migrations()
        pending = self.get_pending_migrations()
        records = self.tracker.get_all_migration_records()

        failed = [r for r in records if r["status"] == "failed"]

        return {
            "applied_count": len(applied),
            "pending_count": len(pending),
            "failed_count": len(failed),
            "applied_migrations": applied,
            "pending_migrations": [m.version for m in pending],
            "failed_migrations": [f["version"] for f in failed],
            "all_records": records,
        }

    def validate_migration_integrity(self) -> Dict[str, Any]:
        """
        Validate integrity of applied migrations.

        Checks that all applied migrations have matching checksums.

        Returns:
            Dictionary with validation results
        """
        self.tracker.ensure_table_exists()

        records = self.tracker.get_all_migration_records()
        results = {"valid": True, "checked": 0, "mismatches": []}

        try:
            migration_files = validator.get_migration_files(str(self.migrations_dir))
        except NotADirectoryError:
            logger.warning("Migrations directory not found for integrity check")
            return results

        migration_dict = {v: f for v, f in migration_files}

        for record in records:
            if record["status"] != "completed":
                continue

            version = record["version"]
            expected_checksum = record["checksum"]
            results["checked"] += 1

            if version not in migration_dict:
                results["valid"] = False
                results["mismatches"].append(
                    {"version": version, "error": "Migration file not found"}
                )
                continue

            file_path = migration_dict[version]
            with open(file_path, "rb") as f:
                content = f.read()
                actual_checksum = validator.calculate_checksum(content)

            if expected_checksum != actual_checksum:
                results["valid"] = False
                results["mismatches"].append(
                    {
                        "version": version,
                        "error": f"Checksum mismatch: expected {expected_checksum}, got {actual_checksum}",
                    }
                )

        return results

    def _execute_migration(
        self, migration: Migration, dry_run: bool = False
    ) -> Dict[str, Any]:
        """
        Internal method to execute a migration.

        Args:
            migration: Migration object to execute
            dry_run: If True, execute without committing or persisting tracker state

        Returns:
            Dictionary with execution result

        Raises:
            Exception: If migration execution fails
        """
        try:
            # For dry-run, skip all tracker operations to avoid polluting migrations_history
            if not dry_run:
                # Validate checksum if already recorded
                existing_status = self.tracker.get_migration_status(migration.version)
                if existing_status:
                    try:
                        # Check if checksum matches
                        with open(migration.file_path, "rb") as f:
                            content = f.read()
                            actual_checksum = validator.calculate_checksum(content)

                        if actual_checksum != existing_status["checksum"]:
                            logger.warning(
                                f"Migration checksum mismatch for {migration.version}: "
                                f"recorded={existing_status['checksum']}, actual={actual_checksum}. "
                                "Proceeding anyway."
                            )
                    except Exception as e:
                        logger.warning(
                            f"Could not verify checksum for migration {migration.version}: {e}"
                        )

                # Record migration start
                self.tracker.record_migration_start(
                    migration.version, migration.name, migration.checksum
                )

            # Execute migration
            runner = MigrationRunner(self.db, self.tracker, dry_run=dry_run)
            runner.validate_migration_file(migration.file_path)
            module = runner.load_migration_module(migration.file_path)
            result = runner.execute_migration_up(
                module, migration.version, migration.name
            )

            # Record success only on real runs
            if not dry_run:
                rollback_sql = None
                if hasattr(module, "down"):
                    rollback_sql = "down() function available"

                self.tracker.record_migration_success(
                    migration.version, result["execution_time_ms"], rollback_sql
                )

                # Invalidate cached migration status so the admin dashboard
                # reflects the change immediately.
                from src.core.database import invalidate_pattern

                invalidate_pattern("migration_status:*")

            return {
                "success": True,
                "version": migration.version,
                "name": migration.name,
                "execution_time_ms": result["execution_time_ms"],
                "dry_run": dry_run,
                "message": result["message"],
            }

        except Exception as e:
            # Record failure only on real runs
            if not dry_run:
                self.tracker.record_migration_failure(migration.version, str(e))
                # Invalidate cached migration status even on failure
                # (failed_count may have changed).
                from src.core.database import invalidate_pattern

                invalidate_pattern("migration_status:*")
            raise
