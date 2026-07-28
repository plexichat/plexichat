"""
Chat tracking database schema - Table definitions for chat tracking module.
"""

import utils.logger as logger

from src.core.database.core.schema_splitter import split_sql_statements


SCHEMA = """
-- Webhook retry queue table
CREATE TABLE IF NOT EXISTS webhook_retry_queue (
    id INTEGER PRIMARY KEY,
    webhook_id INTEGER NOT NULL,
    payload TEXT NOT NULL,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 5,
    next_retry_at INTEGER NOT NULL,
    last_error TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

-- Webhook retry queue indexes
CREATE INDEX IF NOT EXISTS idx_wh_retry_webhook ON webhook_retry_queue(webhook_id);
CREATE INDEX IF NOT EXISTS idx_wh_retry_next ON webhook_retry_queue(next_retry_at);
CREATE INDEX IF NOT EXISTS idx_wh_retry_status ON webhook_retry_queue(status);

-- Push notification tokens table
CREATE TABLE IF NOT EXISTS push_tokens (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL,
    token TEXT NOT NULL,
    platform TEXT NOT NULL,
    device_id TEXT,
    app_version TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    last_used_at INTEGER,
    UNIQUE(user_id, token)
);

-- Push tokens indexes
CREATE INDEX IF NOT EXISTS idx_push_token_user ON push_tokens(user_id);
CREATE INDEX IF NOT EXISTS idx_push_token_platform ON push_tokens(platform);

-- Last chat tracking table
CREATE TABLE IF NOT EXISTS user_last_chat (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL UNIQUE,
    conversation_id INTEGER NOT NULL,
    last_message_id INTEGER,
    scroll_position INTEGER,
    updated_at INTEGER NOT NULL
);

-- Last chat indexes
CREATE INDEX IF NOT EXISTS idx_last_chat_user ON user_last_chat(user_id);

-- Recent chat history table
CREATE TABLE IF NOT EXISTS user_recent_chats (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL,
    conversation_id INTEGER NOT NULL,
    accessed_at INTEGER NOT NULL,
    unread_count INTEGER NOT NULL DEFAULT 0
);

-- Recent chats indexes
CREATE INDEX IF NOT EXISTS idx_recent_chats_user ON user_recent_chats(user_id);
CREATE INDEX IF NOT EXISTS idx_recent_chats_user_conv ON user_recent_chats(user_id, conversation_id);
"""


def create_chat_tracking_tables(db):
    """Create all chat tracking tables."""
    statements = split_sql_statements(SCHEMA)

    for statement in statements:
        if statement:
            try:
                # Convert schema types for PostgreSQL compatibility (INTEGER -> BIGINT, etc.)
                converted = (
                    db.convert_schema(statement)
                    if hasattr(db, "convert_schema")
                    else statement
                )
                db.execute(converted)
            except Exception as e:
                logger.error(f"Failed to execute schema statement: {e}")
                raise

    logger.info("Chat tracking tables created successfully")
