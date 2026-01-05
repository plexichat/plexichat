"""
Database migrations system for PlexiChat.

Provides idempotent migration steps to ensure schema consistency
across different versions of the software.
"""

from typing import Any

import utils.logger as logger


def run_all_migrations(db: Any) -> None:
    """Run all pending schema migrations."""
    logger.info("Checking for database migrations...")

    _migrate_messaging_webhook_id(db)
    _migrate_media_deduplication_v2(db)
    _migrate_threads_conversation_id(db)
    _migrate_srv_members_updated_at(db)

    logger.info("All migrations completed.")


def _migrate_threads_conversation_id(db: Any) -> None:
    """Add conversation_id column to thread_threads if missing."""
    try:
        if not db.table_exists("thread_threads"):
            return

        if not _column_exists(db, "thread_threads", "conversation_id"):
            logger.info("Migration: Adding conversation_id to thread_threads")
            db.execute("ALTER TABLE thread_threads ADD COLUMN conversation_id BIGINT")
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_thread_conversation ON thread_threads(conversation_id)"
            )
            logger.info("Migration: conversation_id added successfully")
    except Exception as e:
        logger.error(f"Migration failed (thread_conversation_id): {e}")


def _migrate_messaging_webhook_id(db: Any) -> None:
    """Add webhook_id column to msg_messages if missing."""
    try:
        # Check if table exists first
        if not db.table_exists("msg_messages"):
            return

        # Check if column exists (cross-database approach)
        db_type = getattr(db, "type", "sqlite")
        column_exists = False

        if db_type == "postgres":
            row = db.fetch_one(
                "SELECT 1 FROM information_schema.columns WHERE table_name = 'msg_messages' AND column_name = 'webhook_id'"
            )
            column_exists = row is not None
        else:
            # SQLite - try to get column info
            rows = db.fetch_all("PRAGMA table_info(msg_messages)")
            column_exists = any(row["name"] == "webhook_id" for row in rows)

        if not column_exists:
            logger.info("Migration: Adding webhook_id to msg_messages")
            db.execute("ALTER TABLE msg_messages ADD COLUMN webhook_id BIGINT")
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_msg_messages_webhook ON msg_messages(webhook_id)"
            )
            logger.info("Migration: webhook_id added successfully")

    except Exception as e:
        logger.error(f"Migration failed (webhook_id): {e}")


def _migrate_media_deduplication_v2(db: Any) -> None:
    """Add phash_value and hash_type columns to media tables."""
    try:
        # 1. media_file_hashes -> phash_value
        if db.table_exists("media_file_hashes"):
            if not _column_exists(db, "media_file_hashes", "phash_value"):
                logger.info("Migration: Adding phash_value to media_file_hashes")
                db.execute("ALTER TABLE media_file_hashes ADD COLUMN phash_value TEXT")
                db.execute(
                    "CREATE INDEX IF NOT EXISTS idx_media_file_hashes_phash ON media_file_hashes(phash_value)"
                )

        # 2. media_hash_reports -> phash_value
        if db.table_exists("media_hash_reports"):
            if not _column_exists(db, "media_hash_reports", "phash_value"):
                logger.info("Migration: Adding phash_value to media_hash_reports")
                db.execute("ALTER TABLE media_hash_reports ADD COLUMN phash_value TEXT")
                db.execute(
                    "CREATE INDEX IF NOT EXISTS idx_media_hash_reports_phash ON media_hash_reports(phash_value)"
                )

        # 3. media_blocked_hashes -> hash_type
        if db.table_exists("media_blocked_hashes"):
            if not _column_exists(db, "media_blocked_hashes", "hash_type"):
                logger.info("Migration: Adding hash_type to media_blocked_hashes")
                db.execute(
                    "ALTER TABLE media_blocked_hashes ADD COLUMN hash_type TEXT NOT NULL DEFAULT 'sha256'"
                )
                db.execute(
                    "CREATE INDEX IF NOT EXISTS idx_media_blocked_hashes_type ON media_blocked_hashes(hash_type)"
                )

    except Exception as e:
        logger.error(f"Migration failed (media_v2): {e}")


def _migrate_srv_members_updated_at(db: Any) -> None:
    """Add updated_at column to srv_members if missing."""
    try:
        if not db.table_exists("srv_members"):
            return

        if not _column_exists(db, "srv_members", "updated_at"):
            logger.info("Migration: Adding updated_at to srv_members")
            # Add column with default value of joined_at for existing rows
            db.execute("ALTER TABLE srv_members ADD COLUMN updated_at BIGINT")
            # Backfill existing rows: set updated_at = joined_at
            db.execute("UPDATE srv_members SET updated_at = joined_at WHERE updated_at IS NULL")
            logger.info("Migration: updated_at added to srv_members successfully")
    except Exception as e:
        logger.error(f"Migration failed (srv_members_updated_at): {e}")


def _column_exists(db: Any, table_name: str, column_name: str) -> bool:
    """Check if a column exists in a table (cross-database)."""
    db_type = getattr(db, "type", "sqlite")
    if db_type == "postgres":
        row = db.fetch_one(
            "SELECT 1 FROM information_schema.columns WHERE table_name = ? AND column_name = ?",
            (table_name, column_name),
        )
        return row is not None
    else:
        rows = db.fetch_all(f"PRAGMA table_info({table_name})")
        return any(row["name"] == column_name for row in rows)
