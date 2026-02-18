"""
Add totp_secret_encrypted column to auth_users table.

This migration adds the missing column required for TOTP 2FA.
"""

import logging
import re

logger = logging.getLogger(__name__)


def up(db):
    """Apply migration to add totp_secret_encrypted."""

    if _table_exists(db, "auth_users"):
        if not _column_exists(db, "auth_users", "totp_secret_encrypted"):
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
    # SQLite does not support DROP COLUMN until very recent versions (3.35.0+)
    # Postgres supports it.
    db_type = getattr(db, "type", "sqlite")
    if db_type == "postgres":
        if _column_exists(db, "auth_users", "totp_secret_encrypted"):
            db.execute("ALTER TABLE auth_users DROP COLUMN totp_secret_encrypted")
    else:
        # SQLite rollback of ADD COLUMN is complex (requires recreating table)
        # and usually avoided in migrations unless critical.
        logger.warning("Down migration for adding column is not supported in SQLite")


def _table_exists(db, table_name: str) -> bool:
    """Strictly check if a table exists."""
    if not re.match(r"^[a-zA-Z0-9_]+$", table_name):
        return False

    db_type = getattr(db, "type", "sqlite")
    if db_type == "postgres":
        row = db.fetch_one(
            "SELECT 1 FROM information_schema.tables WHERE table_name = ?",
            (table_name,),
        )
        return row is not None
    else:
        row = db.fetch_one(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        )
        return row is not None


def _column_exists(db, table_name: str, column_name: str) -> bool:
    """Strictly check if a column exists in a table."""
    if not re.match(r"^[a-zA-Z0-9_]+$", table_name) or not re.match(
        r"^[a-zA-Z0-9_]+$", column_name
    ):
        return False

    db_type = getattr(db, "type", "sqlite")

    # We use db._get_conn() to get direct access for meta-queries
    conn = db._get_conn()
    cursor = conn.cursor()

    try:
        if db_type == "postgres":
            cursor.execute(
                "SELECT 1 FROM information_schema.columns WHERE table_name = %s AND column_name = %s",
                (table_name, column_name),
            )
            return cursor.fetchone() is not None
        else:
            safe_table = (
                db._sanitize_identifier(table_name)
                if hasattr(db, "_sanitize_identifier")
                else table_name
            )
            cursor.execute(f"PRAGMA table_info({safe_table})")
            rows = cursor.fetchall()
            for row in rows:
                if isinstance(row, dict) and row["name"] == column_name:
                    return True
                if not isinstance(row, dict) and row[1] == column_name:
                    return True
            return False
    finally:
        cursor.close()
