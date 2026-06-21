"""
Remove unencrypted internal notes columns.

MIGRATION_METADATA:
{
    "irreversible": true,
    "depends_on": ["030"],
    "description": "Drops unencrypted internal notes columns after encryption verification period",
    "risk_level": "high",
    "backup_required": true
}

WARNING: This migration is irreversible. Only run after:
1. encrypt_internal_notes config has been enabled for sufficient time (1-2 weeks)
2. All existing data has been verified to decrypt correctly
3. No legacy code paths remain
4. Sufficient server uptime has elapsed since migration 030 (configurable delay)
"""

import logging

logger = logging.getLogger(__name__)


def up(db):
    """Drop unencrypted columns after verification period."""
    logger.info(
        "Migration 032: Starting removal of unencrypted internal notes columns. "
        "This operation is irreversible."
    )

    if db.type == "postgres":
        for table, col in [
            ("auth_users", "internal_notes"),
            ("feedback", "internal_notes"),
        ]:
            if db.table_exists(table) and db.column_exists(table, col):
                logger.info(f"Migration 032: Dropping {col} column from {table}")
                db.execute(f"ALTER TABLE {table} DROP COLUMN {col}")
    else:
        logger.warning(
            "Migration 032: SQLite requires manual table recreation for column drop. "
            "Please recreate tables without unencrypted columns."
        )

    logger.info("Migration 032: Completed")


def down(db):
    """Rollback - NOT SUPPORTED."""
    logger.error(
        "Migration 032 rollback: NOT SUPPORTED. "
        "Unencrypted columns were dropped and data cannot be recovered."
    )
    raise RuntimeError(
        "This migration cannot be rolled back - unencrypted columns were dropped"
    )
