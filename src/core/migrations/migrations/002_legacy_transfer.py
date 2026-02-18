"""
Transfer legacy migrations from the old system to the new migration module.

This migration consolidates all existing schema changes from the previous
hardcoded migration system into a single version-controlled migration.
"""

import logging
import re

logger = logging.getLogger(__name__)


def up(db):
    """Apply legacy migrations to ensure schema is up to date."""

    # 1. msg_messages -> webhook_id
    if _table_exists(db, "msg_messages"):
        if not _column_exists(db, "msg_messages", "webhook_id"):
            logger.info("Legacy Migration: Adding webhook_id to msg_messages")
            db.execute("ALTER TABLE msg_messages ADD COLUMN webhook_id BIGINT")
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_msg_messages_webhook ON msg_messages(webhook_id)"
            )

    # 2. Media Deduplication v2
    # media_file_hashes -> phash_value
    if _table_exists(db, "media_file_hashes"):
        if not _column_exists(db, "media_file_hashes", "phash_value"):
            logger.info("Legacy Migration: Adding phash_value to media_file_hashes")
            db.execute("ALTER TABLE media_file_hashes ADD COLUMN phash_value TEXT")
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_media_file_hashes_phash ON media_file_hashes(phash_value)"
            )

    # media_hash_reports -> phash_value
    if _table_exists(db, "media_hash_reports"):
        if not _column_exists(db, "media_hash_reports", "phash_value"):
            logger.info("Legacy Migration: Adding phash_value to media_hash_reports")
            db.execute("ALTER TABLE media_hash_reports ADD COLUMN phash_value TEXT")
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_media_hash_reports_phash ON media_hash_reports(phash_value)"
            )

    # media_blocked_hashes -> hash_type
    if _table_exists(db, "media_blocked_hashes"):
        if not _column_exists(db, "media_blocked_hashes", "hash_type"):
            logger.info("Legacy Migration: Adding hash_type to media_blocked_hashes")
            db.execute(
                "ALTER TABLE media_blocked_hashes ADD COLUMN hash_type TEXT NOT NULL DEFAULT 'sha256'"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_media_blocked_hashes_type ON media_blocked_hashes(hash_type)"
            )

    # 3. thread_threads -> conversation_id
    if _table_exists(db, "thread_threads"):
        if not _column_exists(db, "thread_threads", "conversation_id"):
            logger.info("Legacy Migration: Adding conversation_id to thread_threads")
            db.execute("ALTER TABLE thread_threads ADD COLUMN conversation_id BIGINT")
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_thread_conversation ON thread_threads(conversation_id)"
            )

    # 4. srv_members -> updated_at
    if _table_exists(db, "srv_members"):
        if not _column_exists(db, "srv_members", "updated_at"):
            logger.info("Legacy Migration: Adding updated_at to srv_members")
            db.execute("ALTER TABLE srv_members ADD COLUMN updated_at BIGINT")
            # Backfill existing rows: set updated_at = joined_at
            db.execute(
                "UPDATE srv_members SET updated_at = joined_at WHERE updated_at IS NULL"
            )

    # 5. auth_users -> age_verified, date_of_birth
    if _table_exists(db, "auth_users"):
        if not _column_exists(db, "auth_users", "age_verified"):
            logger.info("Legacy Migration: Adding age_verified to auth_users")
            db.execute(
                "ALTER TABLE auth_users ADD COLUMN age_verified INTEGER DEFAULT 0"
            )

        if not _column_exists(db, "auth_users", "date_of_birth"):
            logger.info("Legacy Migration: Adding date_of_birth to auth_users")
            db.execute("ALTER TABLE auth_users ADD COLUMN date_of_birth TEXT")


def down(db):
    """Rollback legacy migrations (optional)."""
    # Note: Traditional migrations often avoid dropping columns in down()
    # because it causes data loss. We leave it empty or implement carefully.
    pass


def _table_exists(db, table_name: str) -> bool:
    """Strictly check if a table exists."""
    if not re.match(r"^[a-zA-Z0-9_]+$", table_name):
        return False

    db_type = getattr(db, "type", "sqlite")
    if db_type == "postgres":
        row = db.fetch_one(
            "SELECT 1 FROM information_schema.tables WHERE table_name = ?",
            (table_name,),
        )
        return row is not None
    else:
        row = db.fetch_one(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        )
        return row is not None


def _column_exists(db, table_name: str, column_name: str) -> bool:
    """Strictly check if a column exists in a table."""
    if not re.match(r"^[a-zA-Z0-9_]+$", table_name) or not re.match(
        r"^[a-zA-Z0-9_]+$", column_name
    ):
        return False

    db_type = getattr(db, "type", "sqlite")

    # We use db._get_conn() to get direct access for meta-queries
    conn = db._get_conn()
    cursor = conn.cursor()

    try:
        if db_type == "postgres":
            cursor.execute(
                "SELECT 1 FROM information_schema.columns WHERE table_name = %s AND column_name = %s",
                (table_name, column_name),
            )
            return cursor.fetchone() is not None
        else:
            safe_table = (
                db._sanitize_identifier(table_name)
                if hasattr(db, "_sanitize_identifier")
                else table_name
            )
            cursor.execute(f"PRAGMA table_info({safe_table})")
            rows = cursor.fetchall()
            # SQLite fetchall returns list of tuples/dicts depending on cursor type
            # Standard cursor returns tuples, Database class uses dict-like rows
            for row in rows:
                if isinstance(row, dict) and row["name"] == column_name:
                    return True
                if (
                    not isinstance(row, dict) and row[1] == column_name
                ):  # index 1 is name in standard sqlite cursor
                    return True
            return False
    finally:
        cursor.close()
