"""
Add content_index column to msg_messages table for encrypted search.
"""

import logging

logger = logging.getLogger(__name__)


def up(db):
    """Apply the migration."""
    logger.info("Migration 011: Starting content_index column addition")
    # 1. Add column if it doesn't exist
    if not db.column_exists("msg_messages", "content_index"):
        db.execute("ALTER TABLE msg_messages ADD COLUMN content_index TEXT")

    # 2. Add index
    if db.type == "sqlite":
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_msg_messages_content_index ON msg_messages(content_index)"
        )
    else:
        # Postgres index creation is already idempotent with IF NOT EXISTS in our dialect usually,
        # but let's be explicit
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_msg_messages_content_index ON msg_messages(content_index)"
        )


def down(db):
    """Rollback the migration."""
    logger.info("Migration 011 rollback: Starting rollback")
    if db.type == "postgres":
        try:
            if db.index_exists("idx_msg_messages_content_index"):
                db.execute("DROP INDEX idx_msg_messages_content_index")
            if db.column_exists("msg_messages", "content_index"):
                db.execute("ALTER TABLE msg_messages DROP COLUMN content_index")
        except Exception:
            pass
    else:
        # SQLite doesn't support DROP COLUMN easily in older versions,
        # and usually we don't rollback columns in production anyway
        pass
