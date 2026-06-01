"""
Add encrypted columns for medium-sensitivity data.

This migration adds paired *_encrypted TEXT columns to several tables so
new writes can be encrypted at rest using AES-256-GCM via the system
keyring. The original plaintext columns are kept for backwards-compatible
reads; the application code prefers *_encrypted when present and falls
back to the legacy column otherwise.

Columns added:

- auth_devices: name_encrypted, device_type_encrypted, fingerprint_encrypted
- auth_external_accounts: external_id_encrypted
- auth_passkeys: device_name_encrypted
- notif_notifications: content_preview_encrypted
- srv_audit_log: changes_encrypted
- user_settings: value_encrypted

Depends: 040

Version: 041
"""

import logging

logger = logging.getLogger(__name__)


def _add_column_if_missing(db, table: str, column: str) -> None:
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
        db.execute(f"ALTER TABLE {table} ADD COLUMN {column} TEXT")
        logger.info(f"Added column {table}.{column}")
    except Exception as e:
        logger.warning(f"Failed to add column {table}.{column}: {e}")


def up(db):
    """Apply the migration."""
    logger.info("Migration 041: Adding medium-sensitivity encrypted columns")

    additions = [
        ("auth_devices", "name_encrypted"),
        ("auth_devices", "device_type_encrypted"),
        ("auth_devices", "fingerprint_encrypted"),
        ("auth_external_accounts", "external_id_encrypted"),
        ("auth_passkeys", "device_name_encrypted"),
        ("notif_notifications", "content_preview_encrypted"),
        ("srv_audit_log", "changes_encrypted"),
        ("user_settings", "value_encrypted"),
    ]

    for table, column in additions:
        _add_column_if_missing(db, table, column)

    logger.info("Migration 041 completed successfully")


def down(db):
    """Rollback the migration."""
    logger.info("Migration 041 rollback: Starting rollback")

    columns = [
        ("auth_devices", "name_encrypted"),
        ("auth_devices", "device_type_encrypted"),
        ("auth_devices", "fingerprint_encrypted"),
        ("auth_external_accounts", "external_id_encrypted"),
        ("auth_passkeys", "device_name_encrypted"),
        ("notif_notifications", "content_preview_encrypted"),
        ("srv_audit_log", "changes_encrypted"),
        ("user_settings", "value_encrypted"),
    ]

    try:
        if db.type == "postgres":
            for table, column in columns:
                if db.table_exists(table) and db.column_exists(table, column):
                    db.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
        else:
            logger.info(
                "Migration 041 rollback: ADD COLUMN not reversible in SQLite "
                "(columns left in place)"
            )
    except Exception as e:
        logger.warning(f"Migration 041 rollback error: {e}")

    logger.info("Migration 041 rollback completed")
