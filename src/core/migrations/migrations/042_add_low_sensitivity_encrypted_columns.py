"""
Add encrypted columns for low-sensitivity operator-visible data.

This migration adds paired *_encrypted TEXT columns to a few tables whose
data is low-sensitivity (operator-visible audit reasons and bot display
names) but where we still want at-rest protection for defense in depth.

Columns added:

- auth_bots: display_name_encrypted
- srv_bans: reason_encrypted
- auth_ip_blacklist: reason_encrypted

The original plaintext columns are kept for backwards-compatible reads;
the application code prefers *_encrypted when present and falls back
to the legacy column otherwise.

Depends: 041

Version: 042
"""

import logging

logger = logging.getLogger(__name__)


def _add_column_if_missing(db, table: str, column: str) -> None:
    if not db.table_exists(table):
        logger.debug(f"Table {table} does not exist, skipping {column}")
        return
    try:
        if db.column_exists(table, column):
            logger.debug(f"Column {table}.{column} already exists, skipping")
            return
    except Exception:
        pass
    try:
        db.execute(f"ALTER TABLE {table} ADD COLUMN {column} TEXT")
        logger.info(f"Added column {table}.{column}")
    except Exception as e:
        logger.warning(f"Failed to add column {table}.{column}: {e}")


def up(db):
    """Apply the migration."""
    logger.info("Migration 042: Adding low-sensitivity encrypted columns")

    additions = [
        ("auth_bots", "display_name_encrypted"),
        ("srv_bans", "reason_encrypted"),
        ("auth_ip_blacklist", "reason_encrypted"),
    ]

    for table, column in additions:
        _add_column_if_missing(db, table, column)

    logger.info("Migration 042 completed successfully")


def down(db):
    """Rollback the migration."""
    logger.info("Migration 042 rollback: Starting rollback")

    columns = [
        ("auth_bots", "display_name_encrypted"),
        ("srv_bans", "reason_encrypted"),
        ("auth_ip_blacklist", "reason_encrypted"),
    ]

    try:
        if db.type == "postgres":
            for table, column in columns:
                if db.table_exists(table) and db.column_exists(table, column):
                    db.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
        else:
            logger.info(
                "Migration 042 rollback: ADD COLUMN not reversible in SQLite "
                "(columns left in place)"
            )
    except Exception as e:
        logger.warning(f"Migration 042 rollback error: {e}")

    logger.info("Migration 042 rollback completed")
