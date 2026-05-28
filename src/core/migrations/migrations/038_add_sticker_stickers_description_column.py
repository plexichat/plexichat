"""
Add description column to sticker_stickers table.

The sticker_stickers table was created with an initial schema that did not
include a description column. The current schema definition
(src/core/stickers/schema.py) requires this column. This migration adds it
if it doesn't already exist.

Depends: 037

Version: 038
"""

import logging

logger = logging.getLogger(__name__)


def _add_column_if_missing(db, table: str, column: str, ddl: str) -> bool:
    """Add a column to a table if it doesn't already exist. Returns True if added."""
    try:
        if db.column_exists(table, column):
            logger.debug(f"Column {table}.{column} already exists, skipping")
            return False
    except Exception:
        pass
    try:
        from src.core.database import dialect

        safe_table = dialect.sanitize_identifier(table, db.type)
        db.execute(f"ALTER TABLE {safe_table} ADD COLUMN {ddl}")
        logger.info(f"Added column {table}.{column}")
        return True
    except Exception as e:
        logger.warning(f"Failed to add column {table}.{column}: {e}")
        return False


def up(db):
    """Apply the migration."""
    logger.info("Migration 038: Adding sticker_stickers.description column")

    if not db.table_exists("sticker_stickers"):
        logger.warning("sticker_stickers table does not exist, skipping migration")
        return

    _add_column_if_missing(
        db,
        "sticker_stickers",
        "description",
        "description TEXT",
    )

    logger.info("Migration 038: Complete")


def down(db):
    """Rollback the migration."""
    logger.info("Migration 038 rollback: No action needed (ADD COLUMN is additive)")
