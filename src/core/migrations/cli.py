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

import sys
import os
from pathlib import Path

import argparse  # noqa: E402
import logging  # noqa: E402

# Import utilities from common_utils subpackage
import utils.config as config  # noqa: E402
import utils.logger as logger  # noqa: E402
from src.core.database import Database  # noqa: E402
from . import run_migrations, rollback, get_status  # noqa: E402
from .manager import MigrationManager  # noqa: E402
from .cloner import DataCloner  # noqa: E402


logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
# We still use the standard logger for the CLI itself,
# but we must initialize the app's logger for modules that use it.
cli_logger = logging.getLogger(__name__)


def setup_config():
    """Ensure config is setup for the CLI.

    Resolution order:
    1. If config is already loaded (e.g. from config.yaml), use it as-is.
    2. If DATABASE_URL or POSTGRES_HOST env vars are set, build config from them.
    3. Fall back to default SQLite path.

    A warning is logged whenever the database type differs from what the
    existing config (if any) specifies, to help detect misconfiguration.
    """
    import os

    # 1. Try to use existing config if already loaded
    try:
        existing_db = config.get("database")
        if existing_db:
            cli_logger.info(
                "Using existing database config: type=%s",
                existing_db.get("type", "unknown"),
            )
            return
    except RuntimeError:
        pass  # Config not loaded yet

    # 2. Build config from environment or defaults
    db_config = {"type": "sqlite", "path": "data/plexichat.db"}
    env_source = "default"

    pg_host = os.environ.get("POSTGRES_HOST")
    db_url = os.environ.get("DATABASE_URL")

    if db_url and (
        db_url.startswith("postgres://") or db_url.startswith("postgresql://")
    ):
        import urllib.parse

        parsed = urllib.parse.urlparse(db_url)
        db_config = {
            "type": "postgres",
            "postgres": {
                "host": parsed.hostname or "localhost",
                "port": parsed.port or 5432,
                "user": parsed.username or "postgres",
                "password": parsed.password or "",
                "dbname": parsed.path.lstrip("/") if parsed.path else "plexichat",
                "sslmode": "prefer",
            },
        }
        env_source = "DATABASE_URL"
    elif pg_host:
        db_config = {
            "type": "postgres",
            "postgres": {
                "host": pg_host,
                "port": int(os.environ.get("POSTGRES_PORT", 5432)),
                "user": os.environ.get("POSTGRES_USER", "postgres"),
                "password": os.environ.get("POSTGRES_PASSWORD", ""),
                "dbname": os.environ.get("POSTGRES_DBNAME", "plexichat"),
                "sslmode": os.environ.get("POSTGRES_SSLMODE", "prefer"),
            },
        }
        env_source = "POSTGRES_HOST"

    cli_logger.info(
        "Database config resolved from %s: type=%s%s",
        env_source,
        db_config["type"],
        f" host={db_config.get('postgres', {}).get('host', '')}"  # type: ignore[union-attr]
        if db_config["type"] == "postgres"
        else f" path={db_config.get('path', '')}",
    )

    # 3. Check if config.yaml exists on disk — if so, prefer it over env-only config
    try:
        config_path = "config/config.yaml"
        if os.path.isfile(config_path):
            config.setup(
                config_path=config_path,
                default_config={
                    "database": db_config,
                    "logging": {"level": "INFO"},
                },
            )
            # Verify what was actually loaded
            loaded_db = config.get("database", {})
            if loaded_db.get("type") != db_config["type"]:
                cli_logger.warning(
                    "Config file specifies database type=%s but env vars resolved to type=%s. "
                    "Using config file value. If this is wrong, check config/config.yaml.",
                    loaded_db.get("type"),
                    db_config["type"],
                )
        else:
            # No config file — use the env-derived or default config.
            # Always pass a config_path string even if the file doesn't exist,
            # since config.setup() may not accept None.
            config.setup(
                config_path="config/config.yaml",
                default_config={
                    "database": db_config,
                    "logging": {"level": "INFO"},
                },
            )
    except Exception:
        # Last resort: use the env-derived or default config
        config.setup(
            config_path="config/config.yaml",
            default_config={
                "database": db_config,
                "logging": {"level": "INFO"},
            },
        )

    # Initialize logger
    log_config = config.get("logging", {})
    logger.setup(log_dir="logs", level=log_config.get("level", "INFO"))


def create_migration(name: str) -> None:
    """
    Create a new migration file from template.

    Args:
        name: Description of the migration (e.g., "add users table")
    """
    migrations_dir = Path(__file__).parent / "migrations"
    migrations_dir.mkdir(parents=True, exist_ok=True)

    # Find next version number
    existing_files = list(migrations_dir.glob("*.py"))
    existing_versions = []

    for f in existing_files:
        if f.name != "__init__.py":
            version = f.name.split("_")[0]
            try:
                existing_versions.append(int(version))
            except ValueError:
                pass

    next_version = max(existing_versions) + 1 if existing_versions else 1
    version_str = f"{next_version:03d}"

    # Create filename from description
    filename_part = name.lower().replace(" ", "_").replace("-", "_")
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
    raise NotImplementedError(
        "Migration up() is not implemented yet. Add forward SQL/logic for this migration."
    )


def down(db):
    """
    Rollback the migration (reverse direction).
    
    Args:
        db: Database instance
    """
    raise NotImplementedError(
        "Migration down() is not implemented yet. Add rollback SQL/logic for this migration."
    )
'''.format(name=name, description="Add description of what this migration does")

    with open(file_path, "w", encoding="utf-8") as f:
        f.write(template)

    print(f"Created migration: {file_path}")
    print("Edit the file to implement the migration logic")


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

    if status["applied_migrations"]:
        print("Applied Migrations:")
        for record in status["all_records"]:
            if record["status"] == "completed":
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

    if status["failed_migrations"]:
        print("Failed Migrations:")
        for record in status["all_records"]:
            if record["status"] == "failed":
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

    print("\nResults:")
    print(f"  Applied: {result['applied_count']}")
    print(f"  Failed:  {result['failed_count']}")

    for migration_result in result["migrations"]:
        status_str = "OK" if migration_result.get("success", False) else "FAILED"
        print(f"  [{migration_result['version']}] {status_str}")
        if migration_result.get("error"):
            print(f"      Error: {migration_result['error']}")

    if result["dry_run"]:
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

    if result["valid"]:
        print("Status: All migrations are valid")
    else:
        print(f"Status: INVALID - {len(result['mismatches'])} mismatches found")
        for mismatch in result["mismatches"]:
            print(f"  [{mismatch['version']}] {mismatch['error']}")


def migrate_to_postgres(sqlite_path: str, dry_run: bool = False) -> None:
    """
    Execute full migration from SQLite to PostgreSQL.
    """
    print("\nStarting SQLite to PostgreSQL migration...")
    print(f"Source: {sqlite_path}")

    # 1. Connect to Source (SQLite)
    os.environ["SQLITE_PATH"] = sqlite_path
    # Save and clear postgres env vars to force SQLite
    saved_env = {}
    for key in [
        "POSTGRES_HOST",
        "DATABASE_URL",
        "POSTGRES_PORT",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "POSTGRES_DBNAME",
    ]:
        saved_env[key] = os.environ.pop(key, None)
    setup_config()
    source_db = Database()
    source_db.connect()

    # 2. Connect to Target (PostgreSQL) - uses env vars
    # Restore postgres env vars
    for key, value in saved_env.items():
        if value is not None:
            os.environ[key] = value
    setup_config()
    target_db = Database()
    target_db.connect()

    cloner = DataCloner(source_db, target_db)

    # 3. Validation
    if not cloner.validate_source_status():
        print("Error: Source database is not in a stable state. Fix migrations first.")
        sys.exit(1)

    print("\nInitializing PostgreSQL schema...")
    # Initialize schema by running migrations on target
    # We pass target_db to run_migrations
    run_migrations(target_db, dry_run=dry_run)

    if dry_run:
        print("\nDRY RUN: Schema initialized in transaction (will be rolled back).")
        print("Skipping data clone in dry run mode.")
        target_db.rollback()
        return

    # 4. Clone Data
    print("\nCloning data...")
    clone_result = cloner.clone_all()

    # 5. Verification
    print("\nVerifying data integrity...")
    verify_result = cloner.verify_counts()

    if verify_result["valid"]:
        print("\nMigration SUCCESSFUL!")
        print(f"Total tables cloned: {clone_result['table_count']}")
        print(f"Total rows cloned:   {clone_result['total_rows']}")
    else:
        print("\nMigration completed with WARNINGS - row count mismatches found!")
        print(f"Mismatched tables: {verify_result['mismatch_count']}")

    source_db.close()
    target_db.close()


def main():
    """Main CLI entry point."""
    # We defer setup_config to the specific commands to handle overrides
    parser = argparse.ArgumentParser(description="Database migration management CLI")

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # ... (existing commands)
    # create_migration command
    create_parser = subparsers.add_parser(
        "create_migration", help="Create a new migration"
    )
    create_parser.add_argument("name", help="Migration description")

    # list_migrations command
    subparsers.add_parser("list_migrations", help="List all migrations")

    # apply_migrations command
    apply_parser = subparsers.add_parser(
        "apply_migrations", help="Apply pending migrations"
    )
    apply_parser.add_argument(
        "--dry-run", action="store_true", help="Simulate without making changes"
    )

    # rollback_migration command
    rollback_parser = subparsers.add_parser(
        "rollback_migration", help="Rollback a migration"
    )
    rollback_parser.add_argument("version", help="Migration version to rollback")

    # validate_migrations command
    subparsers.add_parser("validate_migrations", help="Validate migration integrity")

    # migrate_to_postgres command
    migrate_parser = subparsers.add_parser(
        "migrate_to_postgres", help="Migrate data from SQLite to PostgreSQL"
    )
    migrate_parser.add_argument(
        "--sqlite-path", default="data/plexichat.db", help="Path to source SQLite file"
    )
    migrate_parser.add_argument(
        "--dry-run", action="store_true", help="Initialize schema but don't clone data"
    )

    args = parser.parse_args()

    if args.command == "create_migration":
        setup_config()
        create_migration(args.name)

    elif args.command == "list_migrations":
        try:
            setup_config()
            db = Database()
            db.connect()
            list_migrations(db)
            db.close()
        except Exception as e:
            print(f"Error: {str(e)}")
            sys.exit(1)

    elif args.command == "apply_migrations":
        try:
            setup_config()
            db = Database()
            db.connect()
            apply_migrations(db, dry_run=args.dry_run)
            db.close()
        except Exception as e:
            print(f"Error: {str(e)}")
            sys.exit(1)

    elif args.command == "rollback_migration":
        try:
            setup_config()
            db = Database()
            db.connect()
            rollback_migration(db, args.version)
            db.close()
        except Exception as e:
            print(f"Error: {str(e)}")
            sys.exit(1)

    elif args.command == "validate_migrations":
        try:
            setup_config()
            db = Database()
            db.connect()
            validate_migrations(db)
            db.close()
        except Exception as e:
            print(f"Error: {str(e)}")
            sys.exit(1)

    elif args.command == "migrate_to_postgres":
        try:
            migrate_to_postgres(args.sqlite_path, dry_run=args.dry_run)
        except Exception as e:
            print(f"Error during migration: {str(e)}")
            import traceback

            traceback.print_exc()
            sys.exit(1)

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
