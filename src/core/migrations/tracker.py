"""
Migration tracker for recording and retrieving migration history.

This module provides the MigrationTracker class for tracking applied migrations,
recording their status, and managing the migrations_history table.
"""

from datetime import datetime
from typing import Optional, List, Dict, Any
from . import schema


class MigrationTracker:
    """Tracks applied migrations and their status in the database."""

    def __init__(self, db):
        """
        Initialize the migration tracker.
        
        Args:
            db: Database instance from plexichat.src.core.database
        """
        self.db = db

    def ensure_table_exists(self):
        """
        Ensure the migrations_history table exists.
        
        Creates the table if it doesn't already exist.
        
        Raises:
            Exception: If table creation fails
        """
        schema.create_tables(self.db)

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
            ('completed',)
        )
        
        return [row[0] if isinstance(row, (list, tuple)) else row['version'] 
                for row in result] if result else []

    def record_migration_start(self, version: str, name: str, checksum: str):
        """
        Record the start of a migration.
        
        Handles both initial migrations and retries of failed migrations by using
        INSERT OR REPLACE to avoid UNIQUE constraint violations.
        
        Args:
            version: Migration version (e.g., '001')
            name: Migration name/description
            checksum: SHA256 checksum of migration file
            
        Raises:
            Exception: If database insert/update fails
        """
        self.ensure_table_exists()
        
        # Use INSERT OR REPLACE to handle both new migrations and retries of failed ones
        self.db.execute(
            """
            INSERT OR REPLACE INTO migrations_history 
            (version, name, checksum, status, applied_by, created_at, updated_at)
            VALUES (
                ?,
                ?,
                ?,
                'running',
                'system',
                COALESCE((SELECT created_at FROM migrations_history WHERE version = ?), CURRENT_TIMESTAMP),
                CURRENT_TIMESTAMP
            )
            """,
            (version, name, checksum, version)
        )

    def record_migration_success(self, version: str, execution_time_ms: int, 
                                 rollback_sql: Optional[str] = None):
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
            ('completed', execution_time_ms, rollback_sql, datetime.utcnow(), version)
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
            ('failed', error_message, datetime.utcnow(), version)
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
            "SELECT * FROM migrations_history WHERE version = ?",
            (version,)
        )
        
        if not result:
            return None
        
        row = result[0]
        
        # Handle both tuple and dict-like row formats
        if isinstance(row, (list, tuple)):
            return {
                'id': row[0],
                'version': row[1],
                'name': row[2],
                'applied_at': row[3],
                'applied_by': row[4],
                'execution_time_ms': row[5],
                'checksum': row[6],
                'status': row[7],
                'rollback_sql': row[8],
                'error_message': row[9],
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
                records.append({
                    'id': row[0],
                    'version': row[1],
                    'name': row[2],
                    'applied_at': row[3],
                    'applied_by': row[4],
                    'execution_time_ms': row[5],
                    'checksum': row[6],
                    'status': row[7],
                    'rollback_sql': row[8],
                    'error_message': row[9],
                })
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
            ('rolled_back', datetime.utcnow(), version)
        )
