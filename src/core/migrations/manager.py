"""
Migration manager for orchestrating database migrations.

This module provides the MigrationManager class which coordinates the
entire migration process including discovery, validation, execution, and tracking.
"""

import logging
from pathlib import Path
from typing import List, Dict, Any

from . import validator
from .tracker import MigrationTracker
from .runner import MigrationRunner

logger = logging.getLogger(__name__)


class Migration:
    """Represents a single migration with metadata."""
    
    def __init__(self, version: str, name: str, file_path: str, checksum: str = ''):
        """
        Initialize migration metadata.
        
        Args:
            version: Version identifier (e.g., '001')
            name: Human-readable name
            file_path: Path to migration file
            checksum: SHA256 checksum of file content
        """
        self.version = version
        self.name = name
        self.file_path = file_path
        self.checksum = checksum


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
        self.migrations_dir = Path(__file__).parent / 'migrations'
        
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
        
        try:
            migration_files = validator.get_migration_files(str(self.migrations_dir))
        except NotADirectoryError:
            logger.warning(f"Migrations directory not found: {self.migrations_dir}")
            return []
        
        pending = []
        for version, file_path in migration_files:
            if version not in applied_set:
                # Calculate checksum
                with open(file_path, 'rb') as f:
                    content = f.read()
                    checksum = validator.calculate_checksum(content)
                
                # Extract migration name from filename
                filename = Path(file_path).stem
                name = filename.split('_', 1)[1] if '_' in filename else filename
                
                pending.append(Migration(version, name, file_path, checksum))
        
        return pending
    
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
        self.tracker.ensure_table_exists()
        
        pending = self.get_pending_migrations()
        
        if not pending:
            logger.info("No pending migrations to apply")
            return {
                'success': True,
                'applied_count': 0,
                'failed_count': 0,
                'migrations': [],
                'dry_run': dry_run
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
            'success': True,
            'applied_count': 0,
            'failed_count': 0,
            'migrations': [],
            'dry_run': dry_run
        }
        
        for migration in pending:
            try:
                result = self._execute_migration(migration, dry_run)
                results['migrations'].append(result)
                results['applied_count'] += 1
            except Exception as e:
                results['success'] = False
                results['failed_count'] += 1
                error_result = {
                    'version': migration.version,
                    'name': migration.name,
                    'success': False,
                    'error': str(e)
                }
                results['migrations'].append(error_result)
                logger.error(f"Failed to apply migration {migration.version}: {str(e)}")
                # Continue with next migration or stop based on configuration
                break
        
        return results
    
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
        
        if status['status'] != 'completed':
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

            return {
                'success': True,
                'version': version,
                'message': f'Migration {version} rolled back successfully'
            }
        
        except Exception as e:
            logger.error(f"Rollback of migration {version} failed: {str(e)}")
            raise
    
    def get_migration_status(self) -> Dict[str, Any]:
        """
        Get current migration status.
        
        Returns:
            Dictionary with applied, pending, failed counts and lists
        """
        self.tracker.ensure_table_exists()
        
        applied = self.tracker.get_applied_migrations()
        pending = self.get_pending_migrations()
        records = self.tracker.get_all_migration_records()
        
        failed = [r for r in records if r['status'] == 'failed']
        
        return {
            'applied_count': len(applied),
            'pending_count': len(pending),
            'failed_count': len(failed),
            'applied_migrations': applied,
            'pending_migrations': [m.version for m in pending],
            'failed_migrations': [f['version'] for f in failed],
            'all_records': records
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
        results = {
            'valid': True,
            'checked': 0,
            'mismatches': []
        }
        
        try:
            migration_files = validator.get_migration_files(str(self.migrations_dir))
        except NotADirectoryError:
            logger.warning("Migrations directory not found for integrity check")
            return results
        
        migration_dict = {v: f for v, f in migration_files}
        
        for record in records:
            if record['status'] != 'completed':
                continue
            
            version = record['version']
            expected_checksum = record['checksum']
            results['checked'] += 1
            
            if version not in migration_dict:
                results['valid'] = False
                results['mismatches'].append({
                    'version': version,
                    'error': 'Migration file not found'
                })
                continue
            
            file_path = migration_dict[version]
            with open(file_path, 'rb') as f:
                content = f.read()
                actual_checksum = validator.calculate_checksum(content)
            
            if expected_checksum != actual_checksum:
                results['valid'] = False
                results['mismatches'].append({
                    'version': version,
                    'error': f'Checksum mismatch: expected {expected_checksum}, got {actual_checksum}'
                })
        
        return results
    
    def _execute_migration(self, migration: Migration, 
                          dry_run: bool = False) -> Dict[str, Any]:
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
                    validator.validate_checksum(
                        migration.version,
                        existing_status['checksum'],
                        migration.checksum
                    )
                
                # Record migration start (uses upsert to handle retries)
                self.tracker.record_migration_start(
                    migration.version,
                    migration.name,
                    migration.checksum
                )
            
            # Execute migration
            runner = MigrationRunner(self.db, self.tracker, dry_run=dry_run)
            runner.validate_migration_file(migration.file_path)
            module = runner.load_migration_module(migration.file_path)
            result = runner.execute_migration_up(module, migration.version, migration.name)
            
            # Record success only on real runs
            if not dry_run:
                rollback_sql = None
                if hasattr(module, 'down'):
                    rollback_sql = 'down() function available'
                
                self.tracker.record_migration_success(
                    migration.version,
                    result['execution_time_ms'],
                    rollback_sql
                )
            
            return {
                'success': True,
                'version': migration.version,
                'name': migration.name,
                'execution_time_ms': result['execution_time_ms'],
                'dry_run': dry_run,
                'message': result['message']
            }
        
        except Exception as e:
            # Record failure only on real runs
            if not dry_run:
                self.tracker.record_migration_failure(migration.version, str(e))
            raise
