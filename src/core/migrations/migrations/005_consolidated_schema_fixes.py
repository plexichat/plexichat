"""
Migration: Consolidated schema fixes for Auth and Messaging.

Description:
    Moves ad-hoc migrations from schema.py files into the formal migration system.
    Adds ip_index to various auth tables and missing columns to messaging tables.
"""

import logging

logger = logging.getLogger(__name__)


def up(db):
    """Apply the migration."""
    logger.info("Migration 005: Starting consolidated schema fixes")
    db_type = getattr(db, "type", "sqlite")

    # 1. Auth migrations: ip_index
    _add_ip_index_to_auth(db, db_type)

    # 2. Messaging migrations: webhook_id, compact_messages_enabled, checksum
    _add_columns_to_messaging(db, db_type)


def down(db):
    """Rollback migration (partial support).

    Restores auth_ip_blacklist from the backup table if it exists.
    Added columns (ip_index, ip_encrypted, webhook_id, etc.) are left in place
    since dropping columns is not supported in all SQLite versions.
    """
    logger.info("Migration 005 rollback: Starting rollback")
    db_type = getattr(db, "type", "sqlite")

    # Try to find and restore the old auth_ip_blacklist table
    if db_type == "sqlite":
        old_tables = db.fetch_all(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'auth_ip_blacklist_old_%'"
        )
    else:
        old_tables = db.fetch_all(
            "SELECT table_name AS name FROM information_schema.tables "
            "WHERE table_name LIKE 'auth_ip_blacklist_old_%'"
        )

    if old_tables:
        # Get the most recent backup table
        old_table_name = (
            max(r["name"] if isinstance(r, dict) else r[0] for r in old_tables)
            if old_tables
            else None
        )
        if old_table_name:
            try:
                db.execute("DROP TABLE IF EXISTS auth_ip_blacklist")
                db.execute(f"ALTER TABLE {old_table_name} RENAME TO auth_ip_blacklist")
                logger.info(
                    "Migration 005 rollback: Restored auth_ip_blacklist from %s",
                    old_table_name,
                )
            except Exception as e:
                logger.warning(
                    "Migration 005 rollback: Could not restore auth_ip_blacklist: %s",
                    e,
                )

    # Added columns are left in place (SQLite can't DROP COLUMN safely)
    logger.info(
        "Migration 005 rollback: Added columns (ip_index, ip_encrypted, etc.) "
        "left in place - not destructive."
    )


def _column_exists(db, table_name: str, column_name: str, db_type: str) -> bool:
    """Check if a column exists in a table."""
    return db.column_exists(table_name, column_name)


def _table_exists(db, table_name: str, db_type: str) -> bool:
    """Check if a table exists."""
    return db.table_exists(table_name)


def _add_ip_index_to_auth(db, db_type: str):
    """Add ip_index to authentication tables and remove old columns."""
    auth_tables = [
        "auth_sessions",
        "auth_known_ips",
        "auth_2fa_challenges",
        "auth_audit_log",
    ]

    for table in auth_tables:
        # Skip if table doesn't exist yet (will be created correctly by schema.py)
        if not db.table_exists(table):
            logger.debug(
                f"Migration 005: Table {table} does not exist, skipping alterations"
            )
            continue

        # 1. Add ip_index if missing
        if not db.column_exists(table, "ip_index"):
            logger.info(f"Migration 005: Adding ip_index to {table}")
            db.execute(f"ALTER TABLE {table} ADD COLUMN ip_index TEXT")
            if table == "auth_known_ips":
                db.execute("UPDATE auth_known_ips SET ip_index = 'legacy' || id")

        # 2. Add ip_encrypted if missing
        if not db.column_exists(table, "ip_encrypted"):
            logger.info(f"Migration 005: Adding ip_encrypted to {table}")
            db.execute(f"ALTER TABLE {table} ADD COLUMN ip_encrypted TEXT")

        # 3. Drop old ip_address column if it exists
        if db.column_exists(table, "ip_address"):
            logger.info(f"Migration 005: Dropping old ip_address column from {table}")
            if db_type == "postgres":
                db.execute(f"ALTER TABLE {table} DROP COLUMN ip_address")
            else:
                # SQLite doesn't support DROP COLUMN in older versions,
                # but we can at least make it nullable or ignore it.
                pass

    # Table: auth_ip_blacklist
    # This one is tricky because ip_index is often the PRIMARY KEY in the new schema.
    if db.table_exists("auth_ip_blacklist"):
        if not db.column_exists("auth_ip_blacklist", "ip_index"):
            logger.info(
                "Migration 005: auth_ip_blacklist missing ip_index. Recreating table."
            )
            import time

            timestamp = int(time.time())
            old_table = f"auth_ip_blacklist_old_{timestamp}"
            try:
                db.execute(f"ALTER TABLE auth_ip_blacklist RENAME TO {old_table}")
                if db_type == "postgres":
                    db.execute("""
                        CREATE TABLE IF NOT EXISTS auth_ip_blacklist (
                            id SERIAL PRIMARY KEY,
                            ip_index TEXT NOT NULL UNIQUE,
                            ip_encrypted TEXT NOT NULL,
                            expires_at BIGINT NOT NULL,
                            reason TEXT,
                            created_at BIGINT NOT NULL
                        )
                    """)
                else:
                    db.execute("""
                        CREATE TABLE IF NOT EXISTS auth_ip_blacklist (
                            id INTEGER PRIMARY KEY,
                            ip_index TEXT NOT NULL UNIQUE,
                            ip_encrypted TEXT NOT NULL,
                            expires_at BIGINT NOT NULL,
                            reason TEXT,
                            created_at BIGINT NOT NULL
                        )
                    """)

                # Migrate existing data from the old table into the new one.
                # Map old ip_address column to ip_index where possible.
                try:
                    if db.table_exists(old_table):
                        # Check which columns exist in the old table
                        if db_type == "postgres":
                            col_rows = db.fetch_all(
                                "SELECT column_name FROM information_schema.columns "
                                "WHERE table_name = ?",
                                (old_table,),
                            )
                            old_cols = {r["column_name"] for r in col_rows}
                        else:
                            # PRAGMA table_info does not support parameterized
                            # queries in SQLite, but old_table is constructed
                            # from a timestamp (safe, not user input). Still,
                            # sanitize it for defense in depth.
                            safe_old = (
                                db._sanitize_identifier(old_table)
                                if hasattr(db, "_sanitize_identifier")
                                else old_table
                            )
                            col_rows = db.fetch_all(f"PRAGMA table_info({safe_old})")
                            old_cols = {r["name"] for r in col_rows}

                        # Build a data migration INSERT that maps old columns to new.
                        # ip_encrypted has a NOT NULL constraint in the new schema,
                        # so we must always provide a value. If the old table has an
                        # ip_encrypted column, use it; otherwise use a placeholder
                        # that signals re-encryption is needed.
                        migrate_cols = []
                        select_exprs = []
                        if "ip_address" in old_cols:
                            migrate_cols.append("ip_index")
                            select_exprs.append("ip_address")
                        if "ip_encrypted" in old_cols:
                            migrate_cols.append("ip_encrypted")
                            select_exprs.append("ip_encrypted")
                        else:
                            # Old table lacks ip_encrypted — use placeholder.
                            # The placeholder satisfies NOT NULL but must be
                            # re-encrypted by the admin after migration.
                            migrate_cols.append("ip_encrypted")
                            select_exprs.append("'migration_pending'")
                        if "reason" in old_cols:
                            migrate_cols.append("reason")
                            select_exprs.append("reason")
                        if "expires_at" in old_cols:
                            migrate_cols.append("expires_at")
                            select_exprs.append("expires_at")
                        if "created_at" in old_cols:
                            migrate_cols.append("created_at")
                            select_exprs.append("created_at")

                        # Always include NOT NULL columns with COALESCE fallbacks
                        # in case the old table is missing them (defensive).
                        if "ip_index" not in migrate_cols:
                            migrate_cols.append("ip_index")
                            # Fallback: generate a placeholder index from id or rowid
                            if "id" in old_cols:
                                select_exprs.append("'legacy_' || id")
                            else:
                                select_exprs.append("'legacy_' || rowid")
                        if "expires_at" not in migrate_cols:
                            migrate_cols.append("expires_at")
                            # Column missing from old table — use far-future
                            # timestamp so the entry stays active until reviewed.
                            select_exprs.append("9999999999")
                        if "created_at" not in migrate_cols:
                            migrate_cols.append("created_at")
                            # Column missing from old table — use current unix timestamp.
                            # Use dialect-appropriate expression.
                            if db_type == "postgres":
                                select_exprs.append(
                                    "EXTRACT(EPOCH FROM NOW())::INTEGER"
                                )
                            else:
                                select_exprs.append("strftime('%s','now')")

                        if migrate_cols:
                            cols_str = ", ".join(migrate_cols)
                            sels_str = ", ".join(select_exprs)
                            db.execute(
                                f"INSERT INTO auth_ip_blacklist ({cols_str}) "
                                f"SELECT {sels_str} FROM {old_table}"
                            )
                            logger.info(
                                "Migration 005: Migrated existing blacklist data from %s",
                                old_table,
                            )
                except Exception as migrate_err:
                    logger.warning(
                        "Migration 005: Could not migrate blacklist data from %s: %s "
                        "- old table preserved for manual recovery",
                        old_table,
                        migrate_err,
                    )

            except Exception as e:
                logger.warning(f"Could not rename/recreate auth_ip_blacklist: {e}")


def _add_columns_to_messaging(db, db_type: str):
    """Add missing columns to messaging tables."""
    # msg_messages.webhook_id
    if db.table_exists("msg_messages"):
        if not db.column_exists("msg_messages", "webhook_id"):
            logger.info("Migration 005: Adding webhook_id to msg_messages")
            col_type = "BIGINT" if db_type == "postgres" else "INTEGER"
            db.execute(f"ALTER TABLE msg_messages ADD COLUMN webhook_id {col_type}")
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_msg_messages_webhook ON msg_messages(webhook_id)"
            )

    # msg_user_settings.compact_messages_enabled
    if db.table_exists("msg_user_settings"):
        if not db.column_exists("msg_user_settings", "compact_messages_enabled"):
            logger.info(
                "Migration 005: Adding compact_messages_enabled to msg_user_settings"
            )
            db.execute(
                "ALTER TABLE msg_user_settings ADD COLUMN compact_messages_enabled INTEGER DEFAULT 1"
            )

    # msg_attachments.checksum
    if db.table_exists("msg_attachments"):
        if not db.column_exists("msg_attachments", "checksum"):
            logger.info("Migration 005: Adding checksum to msg_attachments")
            db.execute("ALTER TABLE msg_attachments ADD COLUMN checksum TEXT")
