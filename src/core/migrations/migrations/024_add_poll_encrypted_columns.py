"""
Add encrypted columns to poll tables for question and options.

This migration adds question_encrypted to poll_polls and text_encrypted to poll_options
to support encryption of poll data. Original columns are kept for backwards compatibility.

Depends: 022
"""

import logging

logger = logging.getLogger(__name__)


def up(db):
    """Apply migration to add encrypted columns to poll tables."""
    logger.info("Migration 024: Starting poll encrypted column addition")

    # Add question_encrypted to poll_polls
    if db.table_exists("poll_polls"):
        if not db.column_exists("poll_polls", "question_encrypted"):
            logger.info("Migration 024: Adding question_encrypted to poll_polls")
            db.execute("ALTER TABLE poll_polls ADD COLUMN question_encrypted TEXT")
        else:
            logger.info(
                "Migration 024: Column question_encrypted already exists in poll_polls"
            )
    else:
        logger.warning("Migration 024: Table poll_polls does not exist, skipping")

    # Add text_encrypted to poll_options
    if db.table_exists("poll_options"):
        if not db.column_exists("poll_options", "text_encrypted"):
            logger.info("Migration 024: Adding text_encrypted to poll_options")
            db.execute("ALTER TABLE poll_options ADD COLUMN text_encrypted TEXT")
        else:
            logger.info(
                "Migration 024: Column text_encrypted already exists in poll_options"
            )
    else:
        logger.warning("Migration 024: Table poll_options does not exist, skipping")


def down(db):
    """Rollback: remove encrypted columns from poll tables."""
    logger.info("Migration 024 rollback: Starting rollback")

    # SQLite does not support DROP COLUMN until very recent versions (3.35.0+)
    # Postgres supports it.
    if db.type == "postgres":
        if db.table_exists("poll_polls") and db.column_exists(
            "poll_polls", "question_encrypted"
        ):
            db.execute("ALTER TABLE poll_polls DROP COLUMN question_encrypted")
        if db.table_exists("poll_options") and db.column_exists(
            "poll_options", "text_encrypted"
        ):
            db.execute("ALTER TABLE poll_options DROP COLUMN text_encrypted")
    else:
        # SQLite rollback of ADD COLUMN is complex (requires recreating table)
        # and usually avoided in migrations unless critical.
        logger.warning(
            "Down migration for adding column is not supported in SQLite. "
            "Manual table recreation required."
        )
