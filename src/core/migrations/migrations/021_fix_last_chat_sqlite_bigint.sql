-- Fix user_last_chat and user_recent_chats to use BIGINT snowflake-compatible identifiers
-- This handles SQLite deployments where migration 020 (PostgreSQL-only) doesn't apply

-- Step 1: Recreate user_last_chat with BIGINT columns (SQLite doesn't support ALTER COLUMN)
CREATE TABLE IF NOT EXISTS _user_last_chat_new (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL,
    conversation_id INTEGER NOT NULL,
    last_message_id INTEGER,
    scroll_position INTEGER DEFAULT 0,
    updated_at INTEGER NOT NULL
);

INSERT INTO _user_last_chat_new (id, user_id, conversation_id, last_message_id, scroll_position, updated_at)
SELECT id, user_id, conversation_id, last_message_id, COALESCE(scroll_position, 0), COALESCE(updated_at, 0)
FROM user_last_chat;

DROP TABLE user_last_chat;
ALTER TABLE _user_last_chat_new RENAME TO user_last_chat;

-- Step 2: Recreate user_recent_chats with BIGINT columns
CREATE TABLE IF NOT EXISTS _user_recent_chats_new (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL,
    conversation_id INTEGER NOT NULL,
    accessed_at INTEGER NOT NULL,
    unread_count INTEGER DEFAULT 0
);

INSERT INTO _user_recent_chats_new (id, user_id, conversation_id, accessed_at, unread_count)
SELECT id, user_id, conversation_id, COALESCE(accessed_at, 0), COALESCE(unread_count, 0)
FROM user_recent_chats;

DROP TABLE user_recent_chats;
ALTER TABLE _user_recent_chats_new RENAME TO user_recent_chats;

-- Verify the tables are recreated correctly
SELECT 'user_last_chat recreated' as status;
SELECT 'user_recent_chats recreated' as status;