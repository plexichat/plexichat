"""
Add encrypted columns for internal notes.

This migration adds encrypted columns for user internal notes and feedback
internal notes. Original columns are kept for backwards compatibility.

Depends: 022
"""

import logging

logger = logging.getLogger(__name__)


def up(db):
    """Apply migration to add encrypted columns."""
    logger.info("Migration 029: Starting internal notes encrypted column addition")

    # User internal notes
    if db.table_exists("auth_users"):
        if not db.column_exists("auth_users", "internal_notes_encrypted"):
            logger.info("Migration 029: Adding internal_notes_encrypted to auth_users")
            db.execute(
                "ALTER TABLE auth_users ADD COLUMN internal_notes_encrypted TEXT"
            )
        else:
            logger.info(
                "Migration 029: Column internal_notes_encrypted already exists in auth_users"
            )
    else:
        logger.warning("Migration 029: Table auth_users does not exist, skipping")

    # Feedback internal notes
    if db.table_exists("feedback"):
        if not db.column_exists("feedback", "internal_notes_encrypted"):
            logger.info("Migration 029: Adding internal_notes_encrypted to feedback")
            db.execute("ALTER TABLE feedback ADD COLUMN internal_notes_encrypted TEXT")
        else:
            logger.info(
                "Migration 029: Column internal_notes_encrypted already exists in feedback"
            )
    else:
        logger.warning("Migration 029: Table feedback does not exist, skipping")


def down(db):
    """Rollback: remove encrypted columns."""
    logger.info("Migration 029 rollback: Starting rollback")

    if db.type == "postgres":
        for table, col in [
            ("auth_users", "internal_notes_encrypted"),
            ("feedback", "internal_notes_encrypted"),
        ]:
            if db.table_exists(table) and db.column_exists(table, col):
                db.execute(f"ALTER TABLE {table} DROP COLUMN {col}")
    else:
        logger.warning(
            "Down migration for adding column is not supported in SQLite. "
            "Manual table recreation required."
        )
