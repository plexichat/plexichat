"""
Webhook database schema - Table definitions for webhooks module.
"""

import utils.logger as logger

from src.core.database.core.schema_splitter import split_sql_statements


SCHEMA = """
-- Webhooks table
CREATE TABLE IF NOT EXISTS webhook_webhooks (
    id INTEGER PRIMARY KEY,
    channel_id INTEGER NOT NULL,
    server_id INTEGER NOT NULL,
    creator_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    webhook_type TEXT NOT NULL DEFAULT 'incoming',
    avatar_url TEXT,
    token_hash TEXT NOT NULL,
    -- Ed25519 signing keys for webhook request verification
    signing_key_public BLOB,  -- 32-byte Ed25519 public key for signature verification
    signing_key_private_encrypted TEXT,  -- Encrypted private key (base64 in encrypted envelope)
    -- When 1, executions WITHOUT a valid Ed25519 signature are rejected
    -- even if the webhook token itself is valid.
    require_signed_deliveries INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

-- Webhooks indexes
CREATE INDEX IF NOT EXISTS idx_webhook_channel ON webhook_webhooks(channel_id);
CREATE INDEX IF NOT EXISTS idx_webhook_server ON webhook_webhooks(server_id);
CREATE INDEX IF NOT EXISTS idx_webhook_creator ON webhook_webhooks(creator_id);

-- Webhook messages table (for tracking webhook-sent messages)
CREATE TABLE IF NOT EXISTS webhook_messages (
    id INTEGER PRIMARY KEY,
    webhook_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    username_override TEXT,
    avatar_override TEXT,
    thread_id INTEGER,
    created_at INTEGER NOT NULL,
    FOREIGN KEY (webhook_id) REFERENCES webhook_webhooks(id) ON DELETE CASCADE
);

-- Webhook messages indexes
CREATE INDEX IF NOT EXISTS idx_webhook_msg_webhook ON webhook_messages(webhook_id);
CREATE INDEX IF NOT EXISTS idx_webhook_msg_message ON webhook_messages(message_id);
CREATE INDEX IF NOT EXISTS idx_webhook_msg_channel ON webhook_messages(channel_id);
"""


def _ensure_require_signed_deliveries_column(db) -> None:
    """Add the require_signed_deliveries column to pre-existing tables.

    ``CREATE TABLE IF NOT EXISTS`` does not alter databases created by an
    older schema, so existing databases get an additive ALTER.

    The column is probed first: on a fresh install the module schema already
    created it, and a duplicate-column ALTER on Postgres aborts the open
    transaction, rolling back every table migration 000 had created in it.
    """
    checker = getattr(db, "column_exists", None)
    if callable(checker):
        try:
            if checker("webhook_webhooks", "require_signed_deliveries"):
                return
        except Exception:
            pass
    statements = (
        "ALTER TABLE webhook_webhooks ADD COLUMN require_signed_deliveries "
        "INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE webhook_webhooks ADD COLUMN IF NOT EXISTS "
        "require_signed_deliveries INTEGER NOT NULL DEFAULT 0",
    )
    for statement in statements:
        try:
            db.execute(statement)
            return
        except Exception:
            continue


def create_tables(db):
    """Create all webhook tables."""
    statements = split_sql_statements(SCHEMA)

    for statement in statements:
        if statement:
            try:
                converted = (
                    db.convert_schema(statement)
                    if hasattr(db, "convert_schema")
                    else statement
                )
                db.execute(converted)
            except Exception as e:
                logger.error(f"Failed to execute schema statement: {e}")
                raise

    _ensure_require_signed_deliveries_column(db)

    logger.info("Webhook tables created successfully")
