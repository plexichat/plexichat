"""
Thread database schema - Table definitions for threads module.
"""

import utils.logger as logger


SCHEMA = """
-- Threads table
CREATE TABLE IF NOT EXISTS thread_threads (
    id INTEGER PRIMARY KEY,
    channel_id INTEGER NOT NULL,
    server_id INTEGER NOT NULL,
    owner_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    thread_type TEXT NOT NULL DEFAULT 'public',
    state TEXT NOT NULL DEFAULT 'active',
    parent_message_id INTEGER,
    auto_archive_duration INTEGER NOT NULL DEFAULT 1440,
    message_count INTEGER DEFAULT 0,
    member_count INTEGER DEFAULT 0,
    created_at INTEGER NOT NULL,
    archived_at INTEGER,
    last_message_at INTEGER,
    locked INTEGER DEFAULT 0,
    deleted INTEGER DEFAULT 0
);

-- Thread indexes
CREATE INDEX IF NOT EXISTS idx_thread_channel ON thread_threads(channel_id);
CREATE INDEX IF NOT EXISTS idx_thread_server ON thread_threads(server_id);
CREATE INDEX IF NOT EXISTS idx_thread_owner ON thread_threads(owner_id);
CREATE INDEX IF NOT EXISTS idx_thread_parent_message ON thread_threads(parent_message_id);
-- Note: idx_thread_state and idx_thread_type removed - low cardinality, rarely queried alone
CREATE INDEX IF NOT EXISTS idx_thread_archived_at ON thread_threads(archived_at);
CREATE INDEX IF NOT EXISTS idx_thread_last_message ON thread_threads(last_message_at);

-- Thread members table
CREATE TABLE IF NOT EXISTS thread_members (
    thread_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    joined_at INTEGER NOT NULL,
    last_read_message_id INTEGER,
    muted INTEGER DEFAULT 0,
    PRIMARY KEY (thread_id, user_id)
);

-- Thread member indexes
CREATE INDEX IF NOT EXISTS idx_thread_member_user ON thread_members(user_id);
CREATE INDEX IF NOT EXISTS idx_thread_member_thread ON thread_members(thread_id);

-- Thread messages table (links to messaging module conversations)
CREATE TABLE IF NOT EXISTS thread_messages (
    id INTEGER PRIMARY KEY,
    thread_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    created_at INTEGER NOT NULL
);

-- Thread message indexes
CREATE INDEX IF NOT EXISTS idx_thread_msg_thread ON thread_messages(thread_id);
CREATE INDEX IF NOT EXISTS idx_thread_msg_message ON thread_messages(message_id);
CREATE INDEX IF NOT EXISTS idx_thread_msg_user ON thread_messages(user_id);
"""


def create_tables(db):
    """Create all thread tables."""
    statements = [s.strip() for s in SCHEMA.split(";") if s.strip()]

    for statement in statements:
        if statement:
            try:
                db.execute(statement)
            except Exception as e:
                logger.error(f"Failed to execute schema statement: {e}")
                raise

    logger.info("Thread tables created successfully")
