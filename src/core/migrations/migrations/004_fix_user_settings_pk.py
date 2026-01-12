"""
Fix user_settings table primary key for PostgreSQL.

Ensures the id column is a simple BIGINT (for Snowflake IDs) and not 
expecting a sequence/autoincrement if it was incorrectly created.
"""

import logging
import re

logger = logging.getLogger(__name__)


def up(db):
    """Apply migration to fix user_settings pk."""
    db_type = getattr(db, "type", "sqlite")
    
    if db_type == "postgres":
        logger.info("Migration 004: Ensuring user_settings.id is BIGINT for Snowflake IDs")
        # In Postgres, if it was created as SERIAL, it has a default nextval()
        # We want to remove that default since we provide Snowflake IDs manually.
        db.execute("ALTER TABLE user_settings ALTER COLUMN id DROP DEFAULT")
        db.execute("ALTER TABLE user_settings ALTER COLUMN id TYPE BIGINT")


def down(db):
    """Rollback: (optional) would involve adding SERIAL back, but we prefer Snowflake IDs."""
    pass


def _table_exists(db, table_name: str) -> bool:
    """Strictly check if a table exists."""
    if not re.match(r"^[a-zA-Z0-9_]+$", table_name):
        return False
        
    db_type = getattr(db, "type", "sqlite")
    if db_type == "postgres":
        row = db.fetch_one(
            "SELECT 1 FROM information_schema.tables WHERE table_name = ?",
            (table_name,)
        )
        return row is not None
    else:
        row = db.fetch_one(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,)
        )
        return row is not None
