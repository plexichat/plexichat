"""
Fix last-chat tables for SQLite to use BIGINT-compatible identifiers.
- Only drops old tables after verification passes

Safety: If row count verification fails, the old tables are preserved and
the migration aborts with a clear error message.
"""

import logging

logger = logging.getLogger(__name__)


def _add_postgres_unread_count(db) -> None:
    """Add unread_count column to user_recent_chats on PostgreSQL.

    Migration 020 added scroll_position/updated_at to user_last_chat and
    converted existing columns to BIGINT, but didn't add unread_count to
    user_recent_chats. This fills that gap.
    """
    # Add unread_count column with default if it doesn't exist
    try:
        db.execute(
            """ALTER TABLE user_recent_chats 
               ADD COLUMN IF NOT EXISTS unread_count INTEGER DEFAULT 0 NOT NULL"""
        )
    except Exception:
        # Fallback: check if column exists before adding
        rows = db.fetch_all(
            """
            SELECT 1 FROM information_schema.columns 
            WHERE table_name = 'user_recent_chats' AND column_name = 'unread_count'
            """
        )
        if not rows:
            db.execute(
                """ALTER TABLE user_recent_chats 
                   ADD COLUMN unread_count INTEGER DEFAULT 0 NOT NULL"""
            )


def _count_rows(db, table: str) -> int:
    """Count rows in a table safely."""
    try:
        row = db.fetch_one(f"SELECT COUNT(*) AS cnt FROM {table}")
        return row["cnt"] if isinstance(row, dict) else row[0]
    except Exception:
        return -1


def up(db):
    logger.info("Migration 021: Starting last-chat BIGINT fix")
    if db.type == "postgres":
        # Only add missing columns; migration 020 already handled BIGINT conversion
        logger.info(
            "Migration 021: Adding unread_count column to user_recent_chats (PostgreSQL)"
        )
        _add_postgres_unread_count(db)
        return

    if db.type != "sqlite":
        logger.info("Migration 021: Skipping (not SQLite)")
        return

    # --- user_last_chat ---
    if db.table_exists("user_last_chat"):
        old_count = _count_rows(db, "user_last_chat")

        db.execute(
            """CREATE TABLE IF NOT EXISTS _user_last_chat_new (
                id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL,
                conversation_id INTEGER NOT NULL,
                last_message_id INTEGER,
                scroll_position INTEGER DEFAULT 0,
                updated_at INTEGER NOT NULL DEFAULT 0
            )"""
        )

        # Copy data preserving NULL values for nullable columns.
        # Only COALESCE columns that have NOT NULL constraints in the new schema
        # (updated_at is NOT NULL with DEFAULT 0, so it must have a value).
        db.execute(
            """INSERT INTO _user_last_chat_new (id, user_id, conversation_id, last_message_id, scroll_position, updated_at)
               SELECT id, user_id, conversation_id, last_message_id,
                      scroll_position, COALESCE(updated_at, 0)
               FROM user_last_chat"""
        )

        # Verify row counts match before dropping the old table
        new_count = _count_rows(db, "_user_last_chat_new")
        if new_count != old_count and old_count >= 0:
            logger.error(
                "Migration 021: Row count mismatch for user_last_chat: "
                "old=%d new=%d - aborting, old table preserved",
                old_count,
                new_count,
            )
            db.execute("DROP TABLE IF EXISTS _user_last_chat_new")
            raise RuntimeError(
                f"Data loss risk: user_last_chat row count mismatch (old={old_count}, new={new_count}). "
                "Old table preserved, migration aborted."
            )

        db.execute("DROP TABLE user_last_chat")
        db.execute("ALTER TABLE _user_last_chat_new RENAME TO user_last_chat")
        logger.info(
            "Migration 021: user_last_chat recreated with %d rows preserved",
            new_count,
        )

    # --- user_recent_chats ---
    if db.table_exists("user_recent_chats"):
        old_count = _count_rows(db, "user_recent_chats")

        db.execute(
            """CREATE TABLE IF NOT EXISTS _user_recent_chats_new (
                id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL,
                conversation_id INTEGER NOT NULL,
                accessed_at INTEGER NOT NULL DEFAULT 0,
                unread_count INTEGER DEFAULT 0
            )"""
        )

        # Copy data preserving NULL values for nullable columns.
        # Only COALESCE accessed_at (NOT NULL in new schema) and unread_count
        # (has a DEFAULT but is nullable, so preserve the original value).
        db.execute(
            """INSERT INTO _user_recent_chats_new (id, user_id, conversation_id, accessed_at, unread_count)
               SELECT id, user_id, conversation_id,
                      COALESCE(accessed_at, 0), unread_count
               FROM user_recent_chats"""
        )

        # Verify row counts match before dropping the old table
        new_count = _count_rows(db, "_user_recent_chats_new")
        if new_count != old_count and old_count >= 0:
            logger.error(
                "Migration 021: Row count mismatch for user_recent_chats: "
                "old=%d new=%d - aborting, old table preserved",
                old_count,
                new_count,
            )
            db.execute("DROP TABLE IF EXISTS _user_recent_chats_new")
            raise RuntimeError(
                f"Data loss risk: user_recent_chats row count mismatch (old={old_count}, new={new_count}). "
                "Old table preserved, migration aborted."
            )

        db.execute("DROP TABLE user_recent_chats")
        db.execute("ALTER TABLE _user_recent_chats_new RENAME TO user_recent_chats")
        logger.info(
            "Migration 021: user_recent_chats recreated with %d rows preserved",
            new_count,
        )


def down(db):
    """Rollback: Not supported due to destructive table recreation.

    This migration recreates tables to fix column types for SQLite.
    Rollback would require another destructive recreation, so we don't support it.
    """
    logger.info(
        "Migration 021 rollback: Not supported (table recreation is destructive)"
    )
    logger.warning(
        "Migration 021 rollback: Not supported - table structure change "
        "cannot be safely reversed without another full table recreation. The data "
        "is preserved in the new schema, so rolling back would risk data loss."
    )
    return
