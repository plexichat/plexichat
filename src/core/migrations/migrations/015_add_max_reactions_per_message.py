"""
Add max_reactions_per_message column to srv_servers table.
"""

import logging

logger = logging.getLogger(__name__)


def up(db):
    """Apply the migration."""
    logger.info("Migration 015: Starting max_reactions_per_message column addition")
    try:
        # Check if column already exists (it's in the initial schema now)
        if not db.column_exists("srv_servers", "max_reactions_per_message"):
            # Add max_reactions_per_message column to srv_servers table.
            db.execute(
                "ALTER TABLE srv_servers ADD COLUMN max_reactions_per_message INTEGER DEFAULT 20"
            )
            logger.info("Migration 015: max_reactions_per_message column added")
        else:
            logger.info(
                "Migration 015: max_reactions_per_message column already exists, skipping"
            )
    except Exception:
        # If column already exists or other error, ignore
        logger.debug(
            "Migration 015: Error adding max_reactions_per_message column",
            exc_info=True,
        )
        pass


def down(db):
    """Rollback the migration.

    For PostgreSQL: Drops the column.
    For SQLite: Column left in place (DROP COLUMN not supported).
    """
    logger.info("Migration 015 rollback: Starting rollback")
    if db.type == "postgres":
        if db.column_exists("srv_servers", "max_reactions_per_message"):
            db.execute("ALTER TABLE srv_servers DROP COLUMN max_reactions_per_message")
            logger.info(
                "Migration 015 rollback: Dropped max_reactions_per_message column (PostgreSQL)"
            )
    else:
        logger.info(
            "Migration 015 rollback: Column left in place (SQLite - DROP COLUMN not supported)"
        )
