"""
Add internal_notes column to auth_users table.
"""

import logging

logger = logging.getLogger(__name__)


def up(db):
    """Apply the migration."""
    logger.info("Migration 010: Starting internal_notes column addition")
    if not db.column_exists("auth_users", "internal_notes"):
        db.execute("ALTER TABLE auth_users ADD COLUMN internal_notes TEXT")


def down(db):
    """Rollback the migration."""
    logger.info("Migration 010 rollback: Starting rollback")
    if db.type == "postgres":
        try:
            if db.column_exists("auth_users", "internal_notes"):
                db.execute("ALTER TABLE auth_users DROP COLUMN internal_notes")
        except Exception:
            pass
