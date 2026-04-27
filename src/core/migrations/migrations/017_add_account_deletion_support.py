"""
Add account deletion support to auth_users.
"""

import logging

logger = logging.getLogger(__name__)


def _add_column_if_missing(db, table: str, column: str, ddl: str) -> None:
    try:
        if db.column_exists(table, column):
            return
    except Exception:
        pass
    try:
        # Sanitize table name to prevent SQL injection in ALTER TABLE
        from src.core.database import dialect

        safe_table = dialect.sanitize_identifier(table, db.type)
        db.execute(f"ALTER TABLE {safe_table} ADD COLUMN {ddl}")
    except Exception:
        pass


def up(db):
    logger.info("Migration 017: Starting account deletion support addition")
    # Add deletion columns to auth_users
    # deletion_status: active, frozen, purged
    _add_column_if_missing(
        db,
        "auth_users",
        "deletion_status",
        "deletion_status TEXT NOT NULL DEFAULT 'active'",
    )
    _add_column_if_missing(db, "auth_users", "deletion_at", "deletion_at BIGINT")

    # Create a database backup of deletion records for last-resort lookup
    # This mirrors the external audit log for redundancy
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS auth_deletion_records (
            id BIGINT PRIMARY KEY,
            user_id BIGINT NOT NULL,
            identifier_hash TEXT NOT NULL,
            status TEXT NOT NULL,
            scheduled_at BIGINT NOT NULL,
            purged_at BIGINT,
            audit_log_checksum TEXT,
            UNIQUE(user_id)
        )
        """
    )

    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_auth_users_deletion_status ON auth_users(deletion_status)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_auth_users_deletion_at ON auth_users(deletion_at)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_auth_deletion_records_user ON auth_deletion_records(user_id)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_auth_deletion_records_hash ON auth_deletion_records(identifier_hash)"
    )


def down(db):
    """Rollback the migration.

    Drops the auth_deletion_records table.
    For PostgreSQL: Drops the deletion columns.
    For SQLite: Columns left in place (DROP COLUMN not supported).
    """
    logger.info("Migration 017 rollback: Starting rollback")
    # Drop the deletion records table
    if db.table_exists("auth_deletion_records"):
        db.execute("DROP TABLE IF EXISTS auth_deletion_records")

    # For PostgreSQL, drop the added columns
    if db.type == "postgres":
        if db.column_exists("auth_users", "deletion_status"):
            db.execute("ALTER TABLE auth_users DROP COLUMN deletion_status")
        if db.column_exists("auth_users", "deletion_at"):
            db.execute("ALTER TABLE auth_users DROP COLUMN deletion_at")
        # Drop indexes
        if db.index_exists("idx_auth_users_deletion_status"):
            db.execute("DROP INDEX idx_auth_users_deletion_status")
        if db.index_exists("idx_auth_users_deletion_at"):
            db.execute("DROP INDEX idx_auth_users_deletion_at")
        logger.info("Migration 017 rollback: Dropped table and columns (PostgreSQL)")
    else:
        # SQLite: Clear column values but leave columns
        if db.column_exists("auth_users", "deletion_status"):
            db.execute("UPDATE auth_users SET deletion_status = 'active'")
        if db.column_exists("auth_users", "deletion_at"):
            db.execute("UPDATE auth_users SET deletion_at = NULL")
        logger.info(
            "Migration 017 rollback: Dropped table, cleared column values (SQLite - columns left in place)"
        )
