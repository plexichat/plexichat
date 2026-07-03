"""
Fix thread_threads schema - add missing columns.

The thread_threads table was initially created with a minimal schema,
but the current schema definition (src/core/threads/schema.py) requires
additional columns like name, name_encrypted, slowmode_interval_ms,
slowmode_updated_by, and slowmode_updated_at that were never added via
migration. This migration adds them if they don't exist.

Also fixes the slowmode column name mismatch: migration 019 added
slowmode_seconds and slowmode_last_msg, but the actual code uses
slowmode_interval_ms, slowmode_updated_by, and slowmode_updated_at.
"""

import logging

logger = logging.getLogger(__name__)


def _add_column_if_missing(db, table: str, column: str, ddl: str) -> bool:
    """Add a column to a table if it doesn't already exist. Returns True if added."""
    try:
        if db.column_exists(table, column):
            logger.debug(f"Column {table}.{column} already exists, skipping")
            return False
    except Exception:
        pass
    try:
        from src.core.database import dialect

        safe_table = dialect.sanitize_identifier(table, db.type)
        db.execute(f"ALTER TABLE {safe_table} ADD COLUMN {ddl}")
        logger.info(f"Added column {table}.{column}")
        return True
    except Exception as e:
        logger.warning(f"Failed to add column {table}.{column}: {e}")
        return False


def up(db):
    """Apply the migration."""
    logger.info("Migration 036: Fixing thread_threads missing columns")

    if not db.table_exists("thread_threads"):
        logger.warning("thread_threads table does not exist, skipping migration")
        return

    # Core thread columns that might be missing if the schema was updated after initial creation
    _add_column_if_missing(
        db,
        "thread_threads",
        "name",
        "name TEXT NOT NULL DEFAULT ''",
    )
    _add_column_if_missing(
        db,
        "thread_threads",
        "name_encrypted",
        "name_encrypted TEXT",
    )
    _add_column_if_missing(
        db,
        "thread_threads",
        "thread_type",
        "thread_type TEXT NOT NULL DEFAULT 'public'",
    )
    _add_column_if_missing(
        db,
        "thread_threads",
        "state",
        "state TEXT NOT NULL DEFAULT 'active'",
    )
    _add_column_if_missing(
        db,
        "thread_threads",
        "auto_archive_duration",
        "auto_archive_duration INTEGER NOT NULL DEFAULT 1440",
    )
    _add_column_if_missing(
        db,
        "thread_threads",
        "message_count",
        "message_count INTEGER DEFAULT 0",
    )
    _add_column_if_missing(
        db,
        "thread_threads",
        "member_count",
        "member_count INTEGER DEFAULT 0",
    )
    _add_column_if_missing(
        db,
        "thread_threads",
        "created_at",
        "created_at INTEGER NOT NULL DEFAULT 0",
    )
    _add_column_if_missing(
        db,
        "thread_threads",
        "archived_at",
        "archived_at INTEGER",
    )
    _add_column_if_missing(
        db,
        "thread_threads",
        "last_message_at",
        "last_message_at INTEGER",
    )
    _add_column_if_missing(
        db,
        "thread_threads",
        "locked",
        "locked INTEGER DEFAULT 0",
    )
    _add_column_if_missing(
        db,
        "thread_threads",
        "deleted",
        "deleted INTEGER DEFAULT 0",
    )

    # Fix slowmode column mismatch: code uses slowmode_interval_ms but migration 019 added slowmode_seconds
    # Keep slowmode_seconds for backward compat but add the correct columns the code uses
    _add_column_if_missing(
        db,
        "thread_threads",
        "slowmode_interval_ms",
        "slowmode_interval_ms INTEGER DEFAULT 0",
    )
    _add_column_if_missing(
        db,
        "thread_threads",
        "slowmode_updated_by",
        "slowmode_updated_by BIGINT",
    )
    _add_column_if_missing(
        db,
        "thread_threads",
        "slowmode_updated_at",
        "slowmode_updated_at INTEGER",
    )

    # Add indices for any new columns
    try:
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_thread_state ON thread_threads(state)"
        )
    except Exception:
        pass
    try:
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_thread_type ON thread_threads(thread_type)"
        )
    except Exception:
        pass
    try:
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_thread_deleted ON thread_threads(deleted)"
        )
    except Exception:
        pass

    logger.info("Migration 036: thread_threads column fixes applied")


def down(db):
    """Rollback the migration."""
    logger.info("Migration 036 rollback: No action needed (ADD COLUMN is additive)")
    # SQLite cannot easily drop columns, and PostgreSQL would require dropping them.
    # Since these are additive changes, rollback is a no-op.
    logger.info("Migration 036 rollback: Complete")
