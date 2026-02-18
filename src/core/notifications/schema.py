"""
Notification database schema - Table definitions for notifications module.
"""

import utils.logger as logger


SCHEMA = """
-- Notifications table
CREATE TABLE IF NOT EXISTS notif_notifications (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL,
    sender_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    conversation_id INTEGER NOT NULL,
    server_id INTEGER,
    channel_id INTEGER,
    thread_id INTEGER,
    mention_type TEXT NOT NULL DEFAULT 'user',
    content_preview TEXT NOT NULL DEFAULT '',
    read INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL
);

-- Notifications indexes
CREATE INDEX IF NOT EXISTS idx_notif_user ON notif_notifications(user_id);
CREATE INDEX IF NOT EXISTS idx_notif_user_read ON notif_notifications(user_id, read);
CREATE INDEX IF NOT EXISTS idx_notif_user_server ON notif_notifications(user_id, server_id);
CREATE INDEX IF NOT EXISTS idx_notif_user_channel ON notif_notifications(user_id, channel_id);
CREATE INDEX IF NOT EXISTS idx_notif_message ON notif_notifications(message_id);
CREATE INDEX IF NOT EXISTS idx_notif_created ON notif_notifications(created_at);

-- User notification settings (global and per-server)
CREATE TABLE IF NOT EXISTS notif_settings (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL,
    server_id INTEGER,
    level TEXT NOT NULL DEFAULT 'all',
    dm_notifications INTEGER NOT NULL DEFAULT 1,
    suppress_everyone INTEGER NOT NULL DEFAULT 0,
    suppress_roles INTEGER NOT NULL DEFAULT 0,
    mobile_push INTEGER NOT NULL DEFAULT 1,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    UNIQUE(user_id, server_id)
);

-- Settings indexes
CREATE INDEX IF NOT EXISTS idx_notif_settings_user ON notif_settings(user_id);
CREATE INDEX IF NOT EXISTS idx_notif_settings_user_server ON notif_settings(user_id, server_id);

-- Channel notification overrides
CREATE TABLE IF NOT EXISTS notif_channel_overrides (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    level TEXT NOT NULL DEFAULT 'all',
    muted_until INTEGER,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    UNIQUE(user_id, channel_id)
);

-- Channel overrides indexes
CREATE INDEX IF NOT EXISTS idx_notif_channel_user ON notif_channel_overrides(user_id);
CREATE INDEX IF NOT EXISTS idx_notif_channel_channel ON notif_channel_overrides(channel_id);

-- Unread tracking per conversation
CREATE TABLE IF NOT EXISTS notif_unread (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL,
    conversation_id INTEGER NOT NULL,
    server_id INTEGER,
    channel_id INTEGER,
    unread_count INTEGER NOT NULL DEFAULT 0,
    mention_count INTEGER NOT NULL DEFAULT 0,
    last_read_message_id INTEGER,
    updated_at INTEGER NOT NULL,
    UNIQUE(user_id, conversation_id)
);

-- Unread indexes
CREATE INDEX IF NOT EXISTS idx_notif_unread_user ON notif_unread(user_id);
CREATE INDEX IF NOT EXISTS idx_notif_unread_user_server ON notif_unread(user_id, server_id);
CREATE INDEX IF NOT EXISTS idx_notif_unread_conv ON notif_unread(conversation_id);
"""


def create_tables(db):
    """Create all notification tables."""
    statements = [s.strip() for s in SCHEMA.split(";") if s.strip()]

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

    logger.info("Notification tables created successfully")
