"""
Migration tracker for recording and retrieving migration history.

This module provides the MigrationTracker class for tracking applied migrations,
recording their status, and managing the migrations_history table.
"""

from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
import time
import logging
import json
import re
import secrets
import os
from . import schema

logger = logging.getLogger(__name__)


class MigrationTracker:
    """Tracks applied migrations and their status in the database."""

    def __init__(self, db):
        """
        Initialize the migration tracker.

        Args:
            db: Database instance from plexichat.src.core.database
        """
        self.db = db
        self._start_time = time.time()
        self._current_uptime_session_id = None

    def ensure_table_exists(self):
        """
        Ensure the migrations_history table exists.

        Creates the table if it doesn't already exist.

        Raises:
            Exception: If table creation fails
        """
        schema.create_tables(self.db)
        self.ensure_lock_table_exists()
        self._start_uptime_session()

    def _start_uptime_session(self):
        """Start a new uptime tracking session."""
        try:
            self.db.execute(
                "INSERT INTO migration_uptime (start_time, uptime_seconds) VALUES (?, 0)",
                (datetime.utcnow(),),
            )
            self._current_uptime_session_id = self.db.fetch_last_insert_id()
            logger.info(
                f"Started uptime tracking session: {self._current_uptime_session_id}"
            )
        except Exception as e:
            logger.warning(f"Failed to start uptime session: {e}")

    def update_uptime(self):
        """Update the current uptime session with elapsed time."""
        if self._current_uptime_session_id is None:
            return

        try:
            elapsed = int(time.time() - self._start_time)
            self.db.execute(
                "UPDATE migration_uptime SET uptime_seconds = ? WHERE id = ?",
                (elapsed, self._current_uptime_session_id),
            )
        except Exception as e:
            logger.warning(f"Failed to update uptime: {e}")

    def get_total_uptime_since(self, since_version: str) -> int:
        """
        Get total server uptime in seconds since a specific migration was applied.

        Args:
            since_version: Migration version to calculate uptime from

        Returns:
            Total uptime in seconds
        """
        try:
            # Get the applied_at timestamp for the migration
            result = self.db.fetch_one(
                "SELECT applied_at FROM migrations_history WHERE version = ? AND status = ?",
                (since_version, "completed"),
            )
            if not result:
                return 0

            applied_at = (
                result[0]
                if isinstance(result, (list, tuple))
                else result.get("applied_at")
            )

            # Sum uptime from all sessions that started after the migration
            result = self.db.fetch_one(
                """
                SELECT COALESCE(SUM(uptime_seconds), 0) as total_uptime
                FROM migration_uptime
                WHERE start_time >= ?
                """,
                (applied_at,),
            )

            total = (
                result[0]
                if isinstance(result, (list, tuple))
                else result.get("total_uptime", 0)
            )
            return int(total) if total else 0
        except Exception as e:
            logger.error(f"Failed to calculate uptime: {e}")
            return 0

    def get_migration_metadata(self, version: str) -> Dict[str, Any]:
        """
        Parse migration metadata from the migration file docstring.

        Args:
            version: Migration version

        Returns:
            Dictionary with metadata fields
        """
        try:
            import importlib.util
            import os

            migration_dir = os.path.dirname(os.path.abspath(__file__))
            migration_file = os.path.join(migration_dir, "migrations", f"{version}.py")

            if not os.path.exists(migration_file):
                return {}

            spec = importlib.util.spec_from_file_location(
                f"migration_{version}", migration_file
            )
            if spec is None or spec.loader is None:
                return {}

            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            docstring = module.__doc__
            if not docstring:
                return {}

            # Parse MIGRATION_METADATA from docstring
            # Look for MIGRATION_METADATA: followed by JSON object
            match = re.search(
                r"MIGRATION_METADATA:\s*\n(\{.*?\})\s*(?:\n\"\"\"|$)",
                docstring,
                re.DOTALL,
            )
            if not match:
                return {}

            try:
                metadata = json.loads(match.group(1))
                return metadata
            except json.JSONDecodeError:
                return {}
        except Exception as e:
            logger.warning(f"Failed to parse metadata for migration {version}: {e}")
            return {}

    def can_run_irreversible_migration(
        self, version: str, delay_hours: int = 168
    ) -> tuple[bool, str]:
        """
        Check if an irreversible migration can be run based on uptime delay.

        Args:
            version: Migration version to check
            delay_hours: Required uptime delay in hours (default 7 days)

        Returns:
            Tuple of (can_run, reason)
        """
        metadata = self.get_migration_metadata(version)
        if not metadata.get("irreversible"):
            return True, "Migration is reversible"

        # Check for emergency override
        override_token = os.environ.get("EMERGENCY_MIGRATION_OVERRIDE")
        if override_token:
            if self._validate_emergency_override(override_token):
                return True, "Emergency override active"
            else:
                return False, "Invalid emergency override token"

        # Check dependencies
        depends_on = metadata.get("depends_on", [])
        if depends_on:
            for dep in depends_on:
                dep_status = self.get_migration_status(dep)
                if not dep_status or dep_status.get("status") != "completed":
                    return False, f"Dependency {dep} not completed"

            # Calculate uptime since the last dependency
            last_dep = depends_on[-1]
            uptime_seconds = self.get_total_uptime_since(last_dep)
            required_seconds = delay_hours * 3600

            if uptime_seconds < required_seconds:
                remaining_hours = (required_seconds - uptime_seconds) / 3600
                return (
                    False,
                    f"Insufficient uptime. Required: {delay_hours}h, "
                    f"Current: {uptime_seconds / 3600:.1f}h, "
                    f"Remaining: {remaining_hours:.1f}h",
                )

        return True, "All checks passed"

    def _validate_emergency_override(self, token: str) -> bool:
        """
        Validate an emergency override token.

        Args:
            token: Token to validate

        Returns:
            True if token is valid and not expired
        """
        try:
            result = self.db.fetch_one(
                """
                SELECT id, used_at, expires_at
                FROM emergency_override_tokens
                WHERE token = ? AND used_at IS NULL AND expires_at > ?
                """,
                (token, datetime.utcnow()),
            )

            if not result:
                return False

            return True
        except Exception as e:
            logger.error(f"Failed to validate emergency override: {e}")
            return False

    def generate_emergency_override(
        self, reason: str, expires_minutes: int = 30
    ) -> str:
        """
        Generate an emergency override token.

        Args:
            reason: Reason for the override
            expires_minutes: Token expiration time in minutes

        Returns:
            Generated token
        """
        token = secrets.token_hex(32)
        expires_at = datetime.utcnow() + timedelta(minutes=expires_minutes)

        try:
            self.db.execute(
                """
                INSERT INTO emergency_override_tokens (token, created_at, expires_at, reason)
                VALUES (?, ?, ?, ?)
                """,
                (token, datetime.utcnow(), expires_at, reason),
            )
            logger.info(f"Generated emergency override token (expires: {expires_at})")
            return token
        except Exception as e:
            logger.error(f"Failed to generate emergency override: {e}")
            raise

    def use_emergency_override(self, token: str, used_by: str):
        """
        Mark an emergency override token as used.

        Args:
            token: Token to mark as used
            used_by: User/admin who used the token
        """
        try:
            self.db.execute(
                """
                UPDATE emergency_override_tokens
                SET used_at = ?, used_by = ?
                WHERE token = ?
                """,
                (datetime.utcnow(), used_by, token),
            )
            logger.warning(f"Emergency override token used by {used_by}")
        except Exception as e:
            logger.error(f"Failed to mark emergency override as used: {e}")

    def log_migration(self, version: str, level: str, message: str):
        """
        Log a migration message to the database.

        Args:
            version: Migration version
            level: Log level (INFO, WARNING, ERROR)
            message: Log message
        """
        try:
            self.db.execute(
                """
                INSERT INTO migration_logs (migration_version, level, message, timestamp)
                VALUES (?, ?, ?, ?)
                """,
                (version, level, message, datetime.utcnow()),
            )
        except Exception as e:
            logger.warning(f"Failed to log migration message: {e}")

    def get_migration_logs(self, version: str) -> List[Dict[str, Any]]:
        """
        Get all logs for a specific migration.

        Args:
            version: Migration version

        Returns:
            List of log entries
        """
        try:
            result = self.db.fetch_all(
                """
                SELECT level, message, timestamp
                FROM migration_logs
                WHERE migration_version = ?
                ORDER BY timestamp ASC
                """,
                (version,),
            )

            if not result:
                return []

            logs = []
            for row in result:
                if isinstance(row, (list, tuple)):
                    logs.append(
                        {"level": row[0], "message": row[1], "timestamp": row[2]}
                    )
                else:
                    logs.append(dict(row))

            return logs
        except Exception as e:
            logger.error(f"Failed to get migration logs: {e}")
            return []

    def get_applied_migrations(self) -> List[str]:
        """
        Get list of applied migration versions.

        Returns:
            List of version strings (e.g., ['001', '002', '003'])

        Raises:
            Exception: If database query fails
        """
        self.ensure_table_exists()

        result = self.db.fetch_all(
            "SELECT version FROM migrations_history WHERE status = ? ORDER BY version",
            ("completed",),
        )

        return (
            [
                row[0] if isinstance(row, (list, tuple)) else row["version"]
                for row in result
            ]
            if result
            else []
        )

    def record_migration_start(self, version: str, name: str, checksum: str):
        """
        Record the start of a migration.

        Handles both initial migrations and retries of failed migrations by using
        upsert to avoid UNIQUE constraint violations.

        Args:
            version: Migration version (e.g., '001')
            name: Migration name/description
            checksum: SHA256 checksum of migration file

        Raises:
            Exception: If database insert/update fails
        """
        self.ensure_table_exists()

        # Get migration metadata
        metadata = self.get_migration_metadata(version)
        is_irreversible = metadata.get("irreversible", False)
        depends_on = (
            json.dumps(metadata.get("depends_on", []))
            if metadata.get("depends_on")
            else None
        )
        metadata_json = json.dumps(metadata) if metadata else None

        # Use upsert to handle both new migrations and retries of failed ones
        # Get existing created_at if it exists to preserve it
        existing = self.get_migration_status(version)
        created_at = existing.get("created_at") if existing else datetime.utcnow()

        self.db.upsert(
            "migrations_history",
            [
                "version",
                "name",
                "checksum",
                "status",
                "applied_by",
                "created_at",
                "updated_at",
                "is_irreversible",
                "depends_on",
                "metadata",
            ],
            (
                version,
                name,
                checksum,
                "running",
                "system",
                created_at,
                datetime.utcnow(),
                1 if is_irreversible else 0,
                depends_on,
                metadata_json,
            ),
            conflict_columns=["version"],
        )

    def record_migration_success(
        self, version: str, execution_time_ms: int, rollback_sql: Optional[str] = None
    ):
        """
        Record successful migration completion.

        Args:
            version: Migration version
            execution_time_ms: Execution time in milliseconds
            rollback_sql: Optional SQL for rolling back the migration

        Raises:
            Exception: If database update fails
        """
        self.ensure_table_exists()

        self.db.execute(
            """
            UPDATE migrations_history 
            SET status = ?, execution_time_ms = ?, rollback_sql = ?, updated_at = ?
            WHERE version = ?
            """,
            ("completed", execution_time_ms, rollback_sql, datetime.utcnow(), version),
        )

    def record_migration_failure(self, version: str, error_message: str):
        """
        Record migration failure.

        Args:
            version: Migration version
            error_message: Error message describing the failure

        Raises:
            Exception: If database update fails
        """
        self.ensure_table_exists()

        self.db.execute(
            """
            UPDATE migrations_history 
            SET status = ?, error_message = ?, updated_at = ?
            WHERE version = ?
            """,
            ("failed", error_message, datetime.utcnow(), version),
        )

    def get_migration_status(self, version: str) -> Optional[Dict[str, Any]]:
        """
        Get status of a specific migration.

        Args:
            version: Migration version

        Returns:
            Dictionary with migration details or None if not found

        Raises:
            Exception: If database query fails
        """
        self.ensure_table_exists()

        result = self.db.fetch_all(
            "SELECT * FROM migrations_history WHERE version = ?", (version,)
        )

        if not result:
            return None

        row = result[0]

        # Handle both tuple and dict-like row formats
        if isinstance(row, (list, tuple)):
            return {
                "id": row[0],
                "version": row[1],
                "name": row[2],
                "applied_at": row[3],
                "applied_by": row[4],
                "execution_time_ms": row[5],
                "checksum": row[6],
                "status": row[7],
                "rollback_sql": row[8],
                "error_message": row[9],
            }
        else:
            return dict(row)

    def get_all_migration_records(self) -> List[Dict[str, Any]]:
        """
        Get all migration records ordered by application time.

        Returns:
            List of migration record dictionaries

        Raises:
            Exception: If database query fails
        """
        self.ensure_table_exists()

        result = self.db.fetch_all(
            "SELECT * FROM migrations_history ORDER BY applied_at ASC"
        )

        if not result:
            return []

        records = []
        for row in result:
            if isinstance(row, (list, tuple)):
                records.append(
                    {
                        "id": row[0],
                        "version": row[1],
                        "name": row[2],
                        "applied_at": row[3],
                        "applied_by": row[4],
                        "execution_time_ms": row[5],
                        "checksum": row[6],
                        "status": row[7],
                        "rollback_sql": row[8],
                        "error_message": row[9],
                    }
                )
            else:
                records.append(dict(row))

        return records

    def record_rollback(self, version: str):
        """
        Record a migration rollback.

        Args:
            version: Migration version being rolled back

        Raises:
            Exception: If database update fails
        """
        self.ensure_table_exists()

        self.db.execute(
            """
            UPDATE migrations_history 
            SET status = ?, updated_at = ?
            WHERE version = ?
            """,
            ("rolled_back", datetime.utcnow(), version),
        )

    def acquire_lock(self, timeout: int = 300) -> bool:
        """
        Acquire a migration lock to prevent concurrent migration runs.

        Uses a database-level advisory lock (PostgreSQL) or a lock table (SQLite).

        Args:
            timeout: Maximum time to wait for lock in seconds

        Returns:
            True if lock was acquired, False otherwise

        Raises:
            Exception: If lock acquisition fails
        """
        self.ensure_table_exists()

        lock_key = 12345  # Arbitrary but consistent key for migration lock
        start_time = time.time()

        while time.time() - start_time < timeout:
            if self.db.type == "postgres":
                # PostgreSQL advisory lock
                result = self.db.fetch_one(
                    "SELECT pg_try_advisory_lock(?)", (lock_key,)
                )
                if result and (
                    result[0]
                    if isinstance(result, (list, tuple))
                    else result.get("pg_try_advisory_lock")
                ):
                    logger.info("Migration lock acquired (PostgreSQL advisory lock)")
                    return True
            else:
                # SQLite: Use a lock table approach
                try:
                    self.db.execute(
                        "INSERT INTO migration_lock (lock_key, locked_at, locked_by) VALUES (?, ?, ?)",
                        (lock_key, datetime.utcnow(), "system"),
                    )
                    logger.info("Migration lock acquired (SQLite lock table)")
                    return True
                except Exception:
                    # Lock already held, check if it's stale
                    lock_info = self.db.fetch_one(
                        "SELECT locked_at FROM migration_lock WHERE lock_key = ?",
                        (lock_key,),
                    )
                    if lock_info:
                        locked_at = (
                            lock_info[0]
                            if isinstance(lock_info, (list, tuple))
                            else lock_info.get("locked_at")
                        )
                        # If lock is older than 10 minutes, consider it stale and acquire it
                        if isinstance(locked_at, datetime):
                            age = (datetime.utcnow() - locked_at).total_seconds()
                        else:
                            age = 0
                        if age > 600:  # 10 minutes
                            logger.warning(
                                f"Found stale migration lock (age: {age}s), acquiring it"
                            )
                            self.db.execute(
                                "UPDATE migration_lock SET locked_at = ?, locked_by = ? WHERE lock_key = ?",
                                (datetime.utcnow(), "system", lock_key),
                            )
                            return True

            # Wait a bit before retrying
            time.sleep(0.5)

        logger.error(f"Failed to acquire migration lock after {timeout} seconds")
        return False

    def release_lock(self):
        """
        Release the migration lock.

        Raises:
            Exception: If lock release fails
        """
        self.ensure_table_exists()

        lock_key = 12345

        if self.db.type == "postgres":
            # PostgreSQL advisory lock
            self.db.execute("SELECT pg_advisory_unlock(?)", (lock_key,))
            logger.info("Migration lock released (PostgreSQL advisory lock)")
        else:
            # SQLite: Delete from lock table
            self.db.execute(
                "DELETE FROM migration_lock WHERE lock_key = ?", (lock_key,)
            )
            logger.info("Migration lock released (SQLite lock table)")

    def ensure_lock_table_exists(self):
        """
        Ensure the migration_lock table exists for SQLite.

        This table is used for cross-process locking on SQLite.
        """
        if self.db.type == "sqlite":
            self.db.execute("""
                CREATE TABLE IF NOT EXISTS migration_lock (
                    lock_key INTEGER PRIMARY KEY,
                    locked_at TIMESTAMP NOT NULL,
                    locked_by TEXT NOT NULL
                )
            """)
