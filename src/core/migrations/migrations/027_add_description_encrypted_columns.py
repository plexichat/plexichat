"""
Add encrypted columns for server descriptions, channel topics, thread names,
and sticker pack descriptions.

This migration adds encrypted columns to support encryption of organizational
and descriptive data. Original columns are kept for backwards compatibility.

Depends: 022
"""

import logging

logger = logging.getLogger(__name__)


def up(db):
    """Apply migration to add encrypted columns."""
    logger.info("Migration 026: Starting description encrypted column addition")

    # Server descriptions
    if db.table_exists("srv_servers"):
        if not db.column_exists("srv_servers", "description_encrypted"):
            logger.info("Migration 026: Adding description_encrypted to srv_servers")
            db.execute("ALTER TABLE srv_servers ADD COLUMN description_encrypted TEXT")
        else:
            logger.info(
                "Migration 026: Column description_encrypted already exists in srv_servers"
            )
    else:
        logger.warning("Migration 026: Table srv_servers does not exist, skipping")

    # Channel topics
    if db.table_exists("srv_channels"):
        if not db.column_exists("srv_channels", "topic_encrypted"):
            logger.info("Migration 026: Adding topic_encrypted to srv_channels")
            db.execute("ALTER TABLE srv_channels ADD COLUMN topic_encrypted TEXT")
        else:
            logger.info(
                "Migration 026: Column topic_encrypted already exists in srv_channels"
            )
    else:
        logger.warning("Migration 026: Table srv_channels does not exist, skipping")

    # Thread names
    if db.table_exists("thread_threads"):
        if not db.column_exists("thread_threads", "name_encrypted"):
            logger.info("Migration 026: Adding name_encrypted to thread_threads")
            db.execute("ALTER TABLE thread_threads ADD COLUMN name_encrypted TEXT")
        else:
            logger.info(
                "Migration 026: Column name_encrypted already exists in thread_threads"
            )
    else:
        logger.warning("Migration 026: Table thread_threads does not exist, skipping")

    # Sticker pack descriptions
    if db.table_exists("sticker_packs"):
        if not db.column_exists("sticker_packs", "description_encrypted"):
            logger.info("Migration 026: Adding description_encrypted to sticker_packs")
            db.execute(
                "ALTER TABLE sticker_packs ADD COLUMN description_encrypted TEXT"
            )
        else:
            logger.info(
                "Migration 026: Column description_encrypted already exists in sticker_packs"
            )
    else:
        logger.warning("Migration 026: Table sticker_packs does not exist, skipping")


def down(db):
    """Rollback: remove encrypted columns."""
    logger.info("Migration 026 rollback: Starting rollback")

    if db.type == "postgres":
        columns = [
            ("srv_servers", "description_encrypted"),
            ("srv_channels", "topic_encrypted"),
            ("thread_threads", "name_encrypted"),
            ("sticker_packs", "description_encrypted"),
        ]
        for table, col in columns:
            if db.table_exists(table) and db.column_exists(table, col):
                db.execute(f"ALTER TABLE {table} DROP COLUMN {col}")
    else:
        logger.warning(
            "Down migration for adding column is not supported in SQLite. "
            "Manual table recreation required."
        )
