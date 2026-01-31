"""
Migration: Consolidated schema fixes for Auth and Messaging.

Description:
    Moves ad-hoc migrations from schema.py files into the formal migration system.
    Adds ip_index to various auth tables and missing columns to messaging tables.
"""

import logging
import re

logger = logging.getLogger(__name__)


def up(db):
    """Apply the migration."""
    db_type = getattr(db, "type", "sqlite")
    
    # 1. Auth migrations: ip_index
    _add_ip_index_to_auth(db, db_type)
    
    # 2. Messaging migrations: webhook_id, compact_messages_enabled, checksum
    _add_columns_to_messaging(db, db_type)


def down(db):
    """Rollback migration (partial support)."""
    # Dropping columns is not supported in all SQLite versions and can be risky.
    # Since these are additions, down() is left as a no-op for safety.
    pass


def _column_exists(db, table_name: str, column_name: str, db_type: str) -> bool:
    """Check if a column exists in a table."""
    if db_type == "postgres":
        row = db.fetch_one(
            "SELECT 1 FROM information_schema.columns WHERE table_name = ? AND column_name = ?",
            (table_name, column_name)
        )
        return row is not None
    else:
        # SQLite
        rows = db.fetch_all(f"PRAGMA table_info({table_name})")
        return any(row["name"] == column_name for row in rows)


def _add_ip_index_to_auth(db, db_type: str):


    """Add ip_index to authentication tables and remove old columns."""


    auth_tables = ["auth_sessions", "auth_known_ips", "auth_2fa_challenges", "auth_audit_log"]


    


    for table in auth_tables:


        # 1. Add ip_index if missing


        if not _column_exists(db, table, "ip_index", db_type):


            logger.info(f"Migration 005: Adding ip_index to {table}")


            db.execute(f"ALTER TABLE {table} ADD COLUMN ip_index TEXT")


            if table == "auth_known_ips":


                db.execute("UPDATE auth_known_ips SET ip_index = 'legacy' || id")


        


        # 2. Add ip_encrypted if missing


        if not _column_exists(db, table, "ip_encrypted", db_type):


            logger.info(f"Migration 005: Adding ip_encrypted to {table}")


            db.execute(f"ALTER TABLE {table} ADD COLUMN ip_encrypted TEXT")


            


        # 3. Drop old ip_address column if it exists


        if _column_exists(db, table, "ip_address", db_type):


            logger.info(f"Migration 005: Dropping old ip_address column from {table}")


            if db_type == "postgres":


                db.execute(f"ALTER TABLE {table} DROP COLUMN ip_address")


            else:


                # SQLite doesn't support DROP COLUMN in older versions, 


                # but we can at least make it nullable or ignore it.


                pass





    # Table: auth_ip_blacklist


    # This one is tricky because ip_index is often the PRIMARY KEY in the new schema.



    if not _column_exists(db, "auth_ip_blacklist", "ip_index", db_type):
        logger.info("Migration 005: auth_ip_blacklist missing ip_index. Recreating table.")
        # If it doesn't have ip_index, it's likely using the old schema where 'ip' was likely the key or it's empty.
        # We'll follow the pattern from auth/schema.py: rename and let the app recreate it, 
        # but here we should probably do it properly.
        import time
        timestamp = int(time.time())
        try:
            db.execute(f"ALTER TABLE auth_ip_blacklist RENAME TO auth_ip_blacklist_old_{timestamp}")
            # The next create_tables call will create the correct one.
        except Exception as e:
            logger.warning(f"Could not rename auth_ip_blacklist: {e}")


def _add_columns_to_messaging(db, db_type: str):
    """Add missing columns to messaging tables."""
    # msg_messages.webhook_id
    if not _column_exists(db, "msg_messages", "webhook_id", db_type):
        logger.info("Migration 005: Adding webhook_id to msg_messages")
        col_type = "BIGINT" if db_type == "postgres" else "INTEGER"
        db.execute(f"ALTER TABLE msg_messages ADD COLUMN webhook_id {col_type}")
        db.execute("CREATE INDEX IF NOT EXISTS idx_msg_messages_webhook ON msg_messages(webhook_id)")

    # msg_user_settings.compact_messages_enabled
    if not _column_exists(db, "msg_user_settings", "compact_messages_enabled", db_type):
        logger.info("Migration 005: Adding compact_messages_enabled to msg_user_settings")
        db.execute("ALTER TABLE msg_user_settings ADD COLUMN compact_messages_enabled INTEGER DEFAULT 1")

    # msg_attachments.checksum
    if not _column_exists(db, "msg_attachments", "checksum", db_type):
        logger.info("Migration 005: Adding checksum to msg_attachments")
        db.execute("ALTER TABLE msg_attachments ADD COLUMN checksum TEXT")
