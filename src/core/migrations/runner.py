"""
Migration runner for executing migrations with transaction support.

This module provides the MigrationRunner class for executing migrations
with proper transaction handling, error recovery, and dry-run support.
"""

import importlib.util
import logging
import time
from pathlib import Path
from typing import Optional, Dict, Any

from . import validator

logger = logging.getLogger(__name__)


class MigrationRunner:
    """Executes migrations with transaction support and error handling."""

    def __init__(self, db, tracker, dry_run: bool = False):
        """
        Initialize the migration runner.
        
        Args:
            db: Database instance
            tracker: MigrationTracker instance
            dry_run: If True, execute in transaction then rollback
        """
        self.db = db
        self.tracker = tracker
        self.dry_run = dry_run

    def load_migration_module(self, file_path: str):
        """
        Dynamically import a migration file as a module.
        
        Args:
            file_path: Path to migration file
            
        Returns:
            Imported migration module
            
        Raises:
            ImportError: If module import fails
        """
        spec = importlib.util.spec_from_file_location(
            Path(file_path).stem,
            file_path
        )
        
        if spec is None or spec.loader is None:
            raise ImportError(f"Could not load migration module: {file_path}")
        
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        
        return module

    def execute_migration_up(self, migration_module, version: str, 
                            name: str) -> Dict[str, Any]:
        """
        Execute migration's up() function.
        
        Args:
            migration_module: Imported migration module
            version: Migration version
            name: Migration name
            
        Returns:
            Dictionary with execution results including timing and status
            
        Raises:
            AttributeError: If migration lacks up() function
            Exception: On migration execution failure
        """
        if not hasattr(migration_module, 'up'):
            raise AttributeError(f"Migration {version} missing up() function")
        
        start_time = time.time()
        
        try:
            if self.dry_run:
                logger.info(f"DRY RUN: Executing migration {version} ({name})")
                self.db.begin_transaction()
                migration_module.up(self.db)
                self.db.rollback()
                logger.info(f"DRY RUN: Migration {version} would execute successfully")
                execution_time_ms = int((time.time() - start_time) * 1000)
                return {
                    'success': True,
                    'version': version,
                    'execution_time_ms': execution_time_ms,
                    'dry_run': True,
                    'message': f'Dry run successful for migration {version}'
                }
            else:
                logger.info(f"Executing migration {version} ({name})")
                self.db.begin_transaction()
                migration_module.up(self.db)
                self.db.commit()
                logger.info(f"Successfully applied migration {version}")
                execution_time_ms = int((time.time() - start_time) * 1000)
                return {
                    'success': True,
                    'version': version,
                    'execution_time_ms': execution_time_ms,
                    'dry_run': False,
                    'message': f'Migration {version} applied successfully'
                }
        
        except Exception as e:
            self.db.rollback()
            logger.error(f"Migration {version} failed: {str(e)}")
            raise

    def execute_migration_down(self, migration_module, version: str) -> Dict[str, Any]:
        """
        Execute migration's down() function if it exists.
        
        Args:
            migration_module: Imported migration module
            version: Migration version
            
        Returns:
            Dictionary with execution results
            
        Raises:
            AttributeError: If migration doesn't have down() function
            Exception: On migration execution failure
        """
        if not hasattr(migration_module, 'down'):
            raise AttributeError(
                f"Migration {version} has no down() function, rollback not available"
            )
        
        start_time = time.time()
        
        try:
            logger.info(f"Rolling back migration {version}")
            self.db.begin_transaction()
            migration_module.down(self.db)
            self.db.commit()
            logger.info(f"Successfully rolled back migration {version}")
            execution_time_ms = int((time.time() - start_time) * 1000)
            return {
                'success': True,
                'version': version,
                'execution_time_ms': execution_time_ms,
                'message': f'Migration {version} rolled back successfully'
            }
        
        except Exception as e:
            self.db.rollback()
            logger.error(f"Rollback of migration {version} failed: {str(e)}")
            raise

    def validate_migration_file(self, file_path: str):
        """
        Validate migration file before execution.
        
        Args:
            file_path: Path to migration file
            
        Raises:
            ValueError: If validation fails
        """
        try:
            validator.validate_migration_file(file_path)
        except (FileNotFoundError, ValueError) as e:
            logger.error(f"Migration validation failed: {str(e)}")
            raise
