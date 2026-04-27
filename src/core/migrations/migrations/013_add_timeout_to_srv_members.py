"""
Add timeout columns to srv_members table.
"""

import logging

logger = logging.getLogger(__name__)


def up(db):
    """Apply the migration."""
    logger.info("Migration 013: Starting timeout columns addition")
    try:
        if not db.column_exists("srv_members", "timeout_until"):
            db.execute("ALTER TABLE srv_members ADD COLUMN timeout_until BIGINT")
        if not db.column_exists("srv_members", "timeout_reason"):
            db.execute("ALTER TABLE srv_members ADD COLUMN timeout_reason TEXT")
    except Exception:
        pass


def down(db):
    """Rollback the migration."""
    logger.info("Migration 013 rollback: Starting rollback")
    if db.type == "postgres":
        try:
            if db.column_exists("srv_members", "timeout_until"):
                db.execute("ALTER TABLE srv_members DROP COLUMN timeout_until")
            if db.column_exists("srv_members", "timeout_reason"):
                db.execute("ALTER TABLE srv_members DROP COLUMN timeout_reason")
        except Exception:
            pass
