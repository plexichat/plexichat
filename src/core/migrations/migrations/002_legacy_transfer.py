"""
Transfer legacy migrations from the old system to the new migration module.

This migration consolidates all existing schema changes from the previous
hardcoded migration system into a single version-controlled migration.
"""

import logging

logger = logging.getLogger(__name__)


def up(db):
    """Apply legacy migrations to ensure schema is up to date."""
    logger.info("Migration 002: Starting legacy transfer migration")

    # 1. msg_messages -> webhook_id
    if db.table_exists("msg_messages"):
        if not db.column_exists("msg_messages", "webhook_id"):
            logger.info("Legacy Migration: Adding webhook_id to msg_messages")
            db.execute("ALTER TABLE msg_messages ADD COLUMN webhook_id BIGINT")
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_msg_messages_webhook ON msg_messages(webhook_id)"
            )

    # 2. Media Deduplication v2
    # media_file_hashes -> phash_value
    if db.table_exists("media_file_hashes"):
        if not db.column_exists("media_file_hashes", "phash_value"):
            logger.info("Legacy Migration: Adding phash_value to media_file_hashes")
            db.execute("ALTER TABLE media_file_hashes ADD COLUMN phash_value TEXT")
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_media_file_hashes_phash ON media_file_hashes(phash_value)"
            )

    # media_hash_reports -> phash_value
    if db.table_exists("media_hash_reports"):
        if not db.column_exists("media_hash_reports", "phash_value"):
            logger.info("Legacy Migration: Adding phash_value to media_hash_reports")
            db.execute("ALTER TABLE media_hash_reports ADD COLUMN phash_value TEXT")
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_media_hash_reports_phash ON media_hash_reports(phash_value)"
            )

    # media_blocked_hashes -> hash_type
    if db.table_exists("media_blocked_hashes"):
        if not db.column_exists("media_blocked_hashes", "hash_type"):
            logger.info("Legacy Migration: Adding hash_type to media_blocked_hashes")
            db.execute(
                "ALTER TABLE media_blocked_hashes ADD COLUMN hash_type TEXT NOT NULL DEFAULT 'sha256'"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_media_blocked_hashes_type ON media_blocked_hashes(hash_type)"
            )

    # 3. thread_threads -> conversation_id
    if db.table_exists("thread_threads"):
        if not db.column_exists("thread_threads", "conversation_id"):
            logger.info("Legacy Migration: Adding conversation_id to thread_threads")
            db.execute("ALTER TABLE thread_threads ADD COLUMN conversation_id BIGINT")
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_thread_conversation ON thread_threads(conversation_id)"
            )

    # 4. srv_members -> updated_at
    if db.table_exists("srv_members"):
        if not db.column_exists("srv_members", "updated_at"):
            logger.info("Legacy Migration: Adding updated_at to srv_members")
            db.execute("ALTER TABLE srv_members ADD COLUMN updated_at BIGINT")
            # Backfill existing rows: set updated_at = joined_at
            db.execute(
                "UPDATE srv_members SET updated_at = joined_at WHERE updated_at IS NULL"
            )

    # 5. auth_users -> age_verified, date_of_birth
    if db.table_exists("auth_users"):
        if not db.column_exists("auth_users", "age_verified"):
            logger.info("Legacy Migration: Adding age_verified to auth_users")
            db.execute(
                "ALTER TABLE auth_users ADD COLUMN age_verified INTEGER DEFAULT 0"
            )

        if not db.column_exists("auth_users", "date_of_birth"):
            logger.info("Legacy Migration: Adding date_of_birth to auth_users")
            db.execute("ALTER TABLE auth_users ADD COLUMN date_of_birth TEXT")


def down(db):
    """Rollback legacy migrations.

    For PostgreSQL: Drops the added columns.
    For SQLite: Columns are left in place (DROP COLUMN not supported).
    """
    logger.info("Migration 002 rollback: Starting rollback")
    if db.type == "postgres":
        # Drop columns if they exist
        if db.column_exists("msg_messages", "webhook_id"):
            db.execute("ALTER TABLE msg_messages DROP COLUMN webhook_id")
        if db.column_exists("media_file_hashes", "phash_value"):
            db.execute("ALTER TABLE media_file_hashes DROP COLUMN phash_value")
        if db.column_exists("media_hash_reports", "phash_value"):
            db.execute("ALTER TABLE media_hash_reports DROP COLUMN phash_value")
        if db.column_exists("media_blocked_hashes", "hash_type"):
            db.execute("ALTER TABLE media_blocked_hashes DROP COLUMN hash_type")
        if db.column_exists("thread_threads", "conversation_id"):
            db.execute("ALTER TABLE thread_threads DROP COLUMN conversation_id")
        if db.column_exists("srv_members", "updated_at"):
            db.execute("ALTER TABLE srv_members DROP COLUMN updated_at")
        if db.column_exists("auth_users", "age_verified"):
            db.execute("ALTER TABLE auth_users DROP COLUMN age_verified")
        if db.column_exists("auth_users", "date_of_birth"):
            db.execute("ALTER TABLE auth_users DROP COLUMN date_of_birth")
        logger.info("Migration 002 rollback: Dropped legacy columns (PostgreSQL)")
    else:
        # SQLite: Columns left in place (DROP COLUMN not supported)
        logger.info(
            "Migration 002 rollback: Columns left in place (SQLite - DROP COLUMN not supported)"
        )
