"""
Add totp_secret_encrypted column to auth_users table.

This migration adds the missing column required for TOTP 2FA.
"""

import logging

logger = logging.getLogger(__name__)


def up(db):
    """Apply migration to add totp_secret_encrypted."""
    logger.info("Migration 003: Starting TOTP secret encrypted column addition")

    if db.table_exists("auth_users"):
        if not db.column_exists("auth_users", "totp_secret_encrypted"):
            logger.info("Migration 003: Adding totp_secret_encrypted to auth_users")
            # In SQLite, adding a column is simple.
            # In Postgres, it's also simple but we should be aware of the type.
            # TEXT is compatible with both.
            db.execute("ALTER TABLE auth_users ADD COLUMN totp_secret_encrypted TEXT")
        else:
            logger.info(
                "Migration 003: Column totp_secret_encrypted already exists in auth_users"
            )


def down(db):
    """Rollback: remove totp_secret_encrypted from auth_users."""
    logger.info("Migration 003 rollback: Starting rollback")
    # SQLite does not support DROP COLUMN until very recent versions (3.35.0+)
    # Postgres supports it.
    if db.type == "postgres":
        if db.column_exists("auth_users", "totp_secret_encrypted"):
            db.execute("ALTER TABLE auth_users DROP COLUMN totp_secret_encrypted")
    else:
        # SQLite rollback of ADD COLUMN is complex (requires recreating table)
        # and usually avoided in migrations unless critical.
        logger.warning("Down migration for adding column is not supported in SQLite")
