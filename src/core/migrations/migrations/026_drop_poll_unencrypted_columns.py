"""
Remove unencrypted question and text columns from poll tables.

MIGRATION_METADATA:
{
    "irreversible": true,
    "depends_on": ["024"],
    "description": "Drops unencrypted poll columns after encryption verification period",
    "risk_level": "high",
    "backup_required": true
}

WARNING: This migration is irreversible. Only run after:
1. encrypt_polls config has been enabled for sufficient time (1-2 weeks)
2. All existing data has been verified to decrypt correctly
3. No legacy code paths remain
4. Sufficient server uptime has elapsed since migration 024 (configurable delay)
"""

import logging

logger = logging.getLogger(__name__)


def up(db):
    """Drop unencrypted columns after verification period."""
    logger.info(
        "Migration 026: Starting removal of unencrypted poll columns. "
        "This operation is irreversible."
    )

    if db.type == "postgres":
        if db.table_exists("poll_polls") and db.column_exists("poll_polls", "question"):
            logger.info("Migration 026: Dropping question column from poll_polls")
            db.execute("ALTER TABLE poll_polls DROP COLUMN question")

        if db.table_exists("poll_options") and db.column_exists("poll_options", "text"):
            logger.info("Migration 026: Dropping text column from poll_options")
            db.execute("ALTER TABLE poll_options DROP COLUMN text")
    else:
        # SQLite requires table recreation for column drop
        logger.warning(
            "Migration 026: SQLite requires manual table recreation for column drop. "
            "Please recreate poll_polls and poll_options tables without question/text columns."
        )

    logger.info("Migration 026: Completed")


def down(db):
    """Rollback - NOT SUPPORTED (data loss would occur)."""
    logger.error(
        "Migration 026 rollback: NOT SUPPORTED. "
        "Unencrypted columns were dropped and data cannot be recovered."
    )
    raise RuntimeError(
        "This migration cannot be rolled back - unencrypted columns were dropped"
    )
