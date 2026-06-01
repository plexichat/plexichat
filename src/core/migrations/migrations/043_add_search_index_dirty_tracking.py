"""
Add source_updated_at to search_message_index for incremental reindex.

Adds a source_updated_at column to search_message_index that tracks the
msg_messages.updated_at value at the time the message was last indexed.
This enables the admin reindex endpoint to skip messages whose source
content is unchanged since the last index, making reindex O(dirty) rather
than O(all messages).

For existing rows, the column defaults to 0 which means "treat as dirty on
next reindex" - so the first reindex after this migration rebuilds the
index (same as before), and subsequent reindexes are fast.

Version: 043
Depends: 042
"""

import logging

logger = logging.getLogger(__name__)


def _add_column_if_missing(
    db, table: str, column: str, col_type: str = "INTEGER"
) -> None:
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
        db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
        logger.info(f"Added column {table}.{column}")
    except Exception as e:
        logger.warning(f"Failed to add column {table}.{column}: {e}")


def up(db):
    """Apply the migration."""
    logger.info("Migration 043: Adding source_updated_at to search_message_index")

    _add_column_if_missing(db, "search_message_index", "source_updated_at", "INTEGER")

    try:
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_search_msg_source_updated "
            "ON search_message_index(source_updated_at)"
        )
        logger.info("Migration 043: Created idx_search_msg_source_updated")
    except Exception as e:
        logger.warning(
            f"Migration 043: Could not create index idx_search_msg_source_updated: {e}"
        )

    logger.info("Migration 043 completed successfully")


def down(db):
    """Rollback the migration."""
    logger.info("Migration 043 rollback: Starting rollback")

    try:
        if db.type == "postgres":
            try:
                db.execute("DROP INDEX IF EXISTS idx_search_msg_source_updated")
            except Exception as e:
                logger.warning(f"Migration 043 rollback: Could not drop index: {e}")
            if db.column_exists("search_message_index", "source_updated_at"):
                db.execute(
                    "ALTER TABLE search_message_index DROP COLUMN source_updated_at"
                )
        else:
            logger.info(
                "Migration 043 rollback: source_updated_at column left in place (SQLite)"
            )
    except Exception as e:
        logger.warning(f"Migration 043 rollback error: {e}")

    logger.info("Migration 043 rollback completed")
