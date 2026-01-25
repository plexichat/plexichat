"""
Database migration system for plexichat.

This module provides a comprehensive migration system for managing database schema changes
with tracking, rollback capability, and safety features.

Usage:
    from src.core.migrations import run_migrations
    
    # Apply all pending migrations
    run_migrations(db)
    
    # Run with dry-run mode (no actual changes)
    run_migrations(db, dry_run=True)
    
    # Rollback a specific migration
    from src.core.migrations import rollback
    rollback(db, version='001')
    
    # Check migration status
    from src.core.migrations import get_status
    status = get_status(db)
"""

import logging
from typing import Dict, Any

from .manager import MigrationManager

logger = logging.getLogger(__name__)

__all__ = [
    'run_migrations',
    'rollback',
    'get_status',
    'MigrationManager',
]


def run_migrations(db, dry_run: bool = False) -> Dict[str, Any]:
    """
    Apply all pending database migrations.
    
    This is the main entry point for the migration system. It discovers pending
    migrations, validates them, and applies them in order.
    
    Args:
        db: Database instance from plexichat.src.core.database
        dry_run: If True, execute migrations without committing changes
        
    Returns:
        Dictionary with migration results including:
        - success: Boolean indicating overall success
        - applied_count: Number of migrations applied
        - failed_count: Number of migrations that failed
        - migrations: List of individual migration results
        - dry_run: Boolean indicating if this was a dry run
        
    Raises:
        Exception: If migration execution fails
        
    Example:
        result = run_migrations(db)
        if result['success']:
            print(f"Applied {result['applied_count']} migrations")
        else:
            print(f"Failed to apply {result['failed_count']} migrations")
    """
    logger.info(f"Starting migration process (dry_run={dry_run})")
    
    manager = MigrationManager(db)
    result = manager.apply_all_pending(dry_run=dry_run)
    
    if result['success']:
        logger.info(
            f"Migrations completed successfully: "
            f"{result['applied_count']} applied, "
            f"{result['failed_count']} failed"
        )
    else:
        logger.error(
            f"Migration process failed: "
            f"{result['applied_count']} applied, "
            f"{result['failed_count']} failed"
        )
    
    return result


def rollback(db, version: str) -> Dict[str, Any]:
    """
    Rollback a specific migration.
    
    Args:
        db: Database instance
        version: Migration version to rollback (e.g., '001')
        
    Returns:
        Dictionary with rollback result
        
    Raises:
        ValueError: If migration not found or doesn't support rollback
        Exception: If rollback execution fails
        
    Example:
        result = rollback(db, version='001')
        if result['success']:
            print(f"Rolled back migration {result['version']}")
    """
    logger.info(f"Rolling back migration {version}")
    
    manager = MigrationManager(db)
    return manager.rollback_migration(version)


def get_status(db) -> Dict[str, Any]:
    """
    Get current migration status.
    
    Args:
        db: Database instance
        
    Returns:
        Dictionary with:
        - applied_count: Number of applied migrations
        - pending_count: Number of pending migrations
        - failed_count: Number of failed migrations
        - applied_migrations: List of applied migration versions
        - pending_migrations: List of pending migration versions
        - failed_migrations: List of failed migration versions
        - all_records: Full migration history records
        
    Example:
        status = get_status(db)
        print(f"Applied: {status['applied_count']}")
        print(f"Pending: {status['pending_count']}")
        print(f"Failed: {status['failed_count']}")
    """
    manager = MigrationManager(db)
    return manager.get_migration_status()
