"""
Add applied_roles column to automod_rules table.
"""

import logging

logger = logging.getLogger(__name__)


def up(db):
    """Apply the migration."""
    logger.info("Migration 012: Starting applied_roles column addition")
    try:
        if not db.column_exists("automod_rules", "applied_roles"):
            db.execute(
                "ALTER TABLE automod_rules ADD COLUMN applied_roles TEXT DEFAULT '[]'"
            )
    except Exception:
        # Ignore errors if column already exists or other issues
        pass


def down(db):
    """Rollback the migration."""
    logger.info("Migration 012 rollback: Starting rollback")
    if db.type == "postgres":
        try:
            if db.column_exists("automod_rules", "applied_roles"):
                db.execute("ALTER TABLE automod_rules DROP COLUMN applied_roles")
        except Exception:
            pass
