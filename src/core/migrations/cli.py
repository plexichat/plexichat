"""
Command-line interface for managing database migrations.

This module provides CLI utilities for migration management:
- create_migration: Generate new migration files
- list_migrations: Show all migrations and their status
- apply_migrations: Apply pending migrations
- rollback_migration: Rollback a specific migration
- validate_migrations: Check migration integrity

Usage:
    python -m src.core.migrations.cli create_migration "add users table"
    python -m src.core.migrations.cli list_migrations
    python -m src.core.migrations.cli apply_migrations --dry-run
    python -m src.core.migrations.cli rollback_migration 001
    python -m src.core.migrations.cli validate_migrations
"""

import argparse
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

# Add project root to path for imports
project_root = Path(__file__).parent.parent.parent.parent.parent
sys.path.insert(0, str(project_root))

from src.core.database import Database
from . import run_migrations, rollback, get_status
from .manager import MigrationManager


import utils.config as config
import utils.logger as logger


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
# We still use the standard logger for the CLI itself, 
# but we must initialize the app's logger for modules that use it.
cli_logger = logging.getLogger(__name__)


def setup_config():
    """Ensure config is setup for the CLI."""
    try:
        # Try to use existing config if possible
        config.get("database")
    except RuntimeError:
        # Not setup, so setup with defaults
        config.setup(config_path="config/config.yaml", default_config={
            "database": {
                "type": "sqlite",
                "path": "data/plexichat.db"
            },
            "logging": {
                "level": "INFO"
            }
        })
        
        # Initialize logger
        log_config = config.get("logging", {})
        logger.setup(
            log_dir="logs",
            level=log_config.get("level", "INFO")
        )
        
        # Apply environment variable overrides (simpler version of main.py)
        import urllib.parse
        db_config = config.get("database", {})
        database_url = os.getenv("DATABASE_URL")
        if database_url:
            if database_url.startswith("postgres://") or database_url.startswith("postgresql://"):
                parsed = urllib.parse.urlparse(database_url)
                db_config["type"] = "postgres"
                db_config["postgres"] = {
                    "host": parsed.hostname or "localhost",
                    "port": parsed.port or 5432,
                    "user": parsed.username or "postgres",
                    "password": parsed.password or "",
                    "dbname": parsed.path.lstrip("/") if parsed.path else "plexichat",
                    "sslmode": "prefer",
                }
                if parsed.query:
                    params = urllib.parse.parse_qs(parsed.query)
                    if "sslmode" in params:
                        db_config["postgres"]["sslmode"] = params["sslmode"][0]
            elif database_url.startswith("sqlite:///"):
                db_config["type"] = "sqlite"
                db_config["path"] = database_url[10:]
            
            config.set("database", db_config)
        elif os.getenv("POSTGRES_HOST"):
            # Minimal support for other PG env vars if needed
            if "postgres" not in db_config:
                db_config["postgres"] = {}
            db_config["type"] = "postgres"
            db_config["postgres"]["host"] = os.getenv("POSTGRES_HOST")
            db_config["postgres"]["user"] = os.getenv("POSTGRES_USER", "postgres")
            db_config["postgres"]["password"] = os.getenv("POSTGRES_PASSWORD", "")
            db_config["postgres"]["dbname"] = os.getenv("POSTGRES_DBNAME", "plexichat")
            config.set("database", db_config)


def create_migration(name: str) -> None:
    """
    Create a new migration file from template.
    
    Args:
        name: Description of the migration (e.g., "add users table")
    """
    migrations_dir = Path(__file__).parent / 'migrations'
    migrations_dir.mkdir(parents=True, exist_ok=True)
    
    # Find next version number
    existing_files = list(migrations_dir.glob('*.py'))
    existing_versions = []
    
    for f in existing_files:
        if f.name != '__init__.py':
            version = f.name.split('_')[0]
            try:
                existing_versions.append(int(version))
            except ValueError:
                pass
    
    next_version = max(existing_versions) + 1 if existing_versions else 1
    version_str = f"{next_version:03d}"
    
    # Create filename from description
    filename_part = name.lower().replace(' ', '_').replace('-', '_')
    filename = f"{version_str}_{filename_part}.py"
    file_path = migrations_dir / filename
    
    # Create template
    template = '''"""
Migration: {name}

Description:
    {description}
"""


def up(db):
    """
    Apply the migration (forward direction).
    
    Args:
        db: Database instance from plexichat.src.core.database
    """
    # TODO: Implement forward migration
    pass


def down(db):
    """
    Rollback the migration (reverse direction).
    
    Args:
        db: Database instance
    """
    # TODO: Implement rollback migration
    pass
'''.format(
        name=name,
        description=f"Add description of what this migration does"
    )
    
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(template)
    
    print(f"Created migration: {file_path}")
    print(f"Edit the file to implement the migration logic")


def list_migrations(db) -> None:
    """
    List all migrations and their status.
    
    Args:
        db: Database instance
    """
    status = get_status(db)
    
    print("\nMigration Status")
    print("=" * 80)
    print(f"Applied:  {status['applied_count']}")
    print(f"Pending:  {status['pending_count']}")
    print(f"Failed:   {status['failed_count']}")
    print()
    
    if status['applied_migrations']:
        print("Applied Migrations:")
        for record in status['all_records']:
            if record['status'] == 'completed':
                print(f"  [{record['version']}] {record['name']}")
                print(f"      Applied: {record['applied_at']}")
                print(f"      Time: {record['execution_time_ms']}ms")
        print()
    
    manager = MigrationManager(db)
    pending = manager.get_pending_migrations()
    
    if pending:
        print("Pending Migrations:")
        for migration in pending:
            print(f"  [{migration.version}] {migration.name}")
        print()
    
    if status['failed_migrations']:
        print("Failed Migrations:")
        for record in status['all_records']:
            if record['status'] == 'failed':
                print(f"  [{record['version']}] {record['name']}")
                print(f"      Error: {record['error_message']}")
        print()


def apply_migrations(db, dry_run: bool = False) -> None:
    """
    Apply all pending migrations.
    
    Args:
        db: Database instance
        dry_run: If True, don't actually apply migrations
    """
    mode = "DRY RUN" if dry_run else "APPLY"
    print(f"\n{mode}: Applying pending migrations...")
    
    result = run_migrations(db, dry_run=dry_run)
    
    print(f"\nResults:")
    print(f"  Applied: {result['applied_count']}")
    print(f"  Failed:  {result['failed_count']}")
    
    for migration_result in result['migrations']:
        status_str = "OK" if migration_result.get('success', False) else "FAILED"
        print(f"  [{migration_result['version']}] {status_str}")
        if migration_result.get('error'):
            print(f"      Error: {migration_result['error']}")
    
    if result['dry_run']:
        print("\nThis was a dry run - no changes were made")


def rollback_migration(db, version: str) -> None:
    """
    Rollback a specific migration.
    
    Args:
        db: Database instance
        version: Migration version to rollback (e.g., '001')
    """
    print(f"\nRolling back migration {version}...")
    
    try:
        result = rollback(db, version)
        print(f"Success: {result['message']}")
    except Exception as e:
        print(f"Error: {str(e)}")
        sys.exit(1)


def validate_migrations(db) -> None:
    """
    Validate migration integrity.
    
    Args:
        db: Database instance
    """
    print("\nValidating migration integrity...")
    
    manager = MigrationManager(db)
    result = manager.validate_migration_integrity()
    
    print(f"Checked: {result['checked']} migrations")
    
    if result['valid']:
        print("Status: All migrations are valid")
    else:
        print(f"Status: INVALID - {len(result['mismatches'])} mismatches found")
        for mismatch in result['mismatches']:
            print(f"  [{mismatch['version']}] {mismatch['error']}")


def main():
    """Main CLI entry point."""
    setup_config()
    parser = argparse.ArgumentParser(
        description='Database migration management CLI'
    )
    
    subparsers = parser.add_subparsers(dest='command', help='Command to run')
    
    # create_migration command
    create_parser = subparsers.add_parser('create_migration', help='Create a new migration')
    create_parser.add_argument('name', help='Migration description')
    
    # list_migrations command
    subparsers.add_parser('list_migrations', help='List all migrations')
    
    # apply_migrations command
    apply_parser = subparsers.add_parser('apply_migrations', help='Apply pending migrations')
    apply_parser.add_argument('--dry-run', action='store_true', help='Simulate without making changes')
    
    # rollback_migration command
    rollback_parser = subparsers.add_parser('rollback_migration', help='Rollback a migration')
    rollback_parser.add_argument('version', help='Migration version to rollback')
    
    # validate_migrations command
    subparsers.add_parser('validate_migrations', help='Validate migration integrity')
    
    args = parser.parse_args()
    
    if args.command == 'create_migration':
        create_migration(args.name)
    
    elif args.command == 'list_migrations':
        try:
            db = Database()
            db.connect()
            list_migrations(db)
            db.close()
        except Exception as e:
            print(f"Error: {str(e)}")
            sys.exit(1)
    
    elif args.command == 'apply_migrations':
        try:
            db = Database()
            db.connect()
            apply_migrations(db, dry_run=args.dry_run)
            db.close()
        except Exception as e:
            print(f"Error: {str(e)}")
            sys.exit(1)
    
    elif args.command == 'rollback_migration':
        try:
            db = Database()
            db.connect()
            rollback_migration(db, args.version)
            db.close()
        except Exception as e:
            print(f"Error: {str(e)}")
            sys.exit(1)
    
    elif args.command == 'validate_migrations':
        try:
            db = Database()
            db.connect()
            validate_migrations(db)
            db.close()
        except Exception as e:
            print(f"Error: {str(e)}")
            sys.exit(1)
    
    else:
        parser.print_help()


if __name__ == '__main__':
    main()
