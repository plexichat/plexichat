"""
Webhook database schema - Table definitions for webhooks module.
"""

import utils.logger as logger


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


def create_tables(db):
    """Create all webhook tables."""
    statements = [s.strip() for s in SCHEMA.split(";") if s.strip()]

    for statement in statements:
        if statement:
            try:
                converted = db.convert_schema(statement) if hasattr(db, 'convert_schema') else statement
                db.execute(converted)
            except Exception as e:
                logger.error(f"Failed to execute schema statement: {e}")
                raise

    logger.info("Webhook tables created successfully")
