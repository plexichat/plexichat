"""
Presence database schema - Table definitions for presence module.
"""

import utils.logger as logger


SCHEMA = """
-- Presence table (user status and last seen)
CREATE TABLE IF NOT EXISTS pres_presence (
    user_id INTEGER PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'offline',
    last_seen INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

-- Presence indexes
-- Note: idx_pres_presence_status removed - low cardinality (online/offline/idle/dnd), rarely queried alone
CREATE INDEX IF NOT EXISTS idx_pres_presence_last_seen ON pres_presence(last_seen);

-- Custom status table
CREATE TABLE IF NOT EXISTS pres_custom_status (
    user_id INTEGER PRIMARY KEY,
    text TEXT NOT NULL,
    emoji TEXT,
    expires_at INTEGER,
    created_at INTEGER NOT NULL
);

-- Activity table
CREATE TABLE IF NOT EXISTS pres_activity (
    user_id INTEGER PRIMARY KEY,
    activity_type TEXT NOT NULL,
    name TEXT NOT NULL,
    details TEXT,
    url TEXT,
    state TEXT,
    start_timestamp INTEGER,
    end_timestamp INTEGER,
    large_image TEXT,
    large_text TEXT,
    small_image TEXT,
    small_text TEXT,
    created_at INTEGER NOT NULL
);

-- Typing indicators table
CREATE TABLE IF NOT EXISTS pres_typing (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    started_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    UNIQUE(user_id, channel_id)
);

-- Typing indexes
CREATE INDEX IF NOT EXISTS idx_pres_typing_channel ON pres_typing(channel_id);
CREATE INDEX IF NOT EXISTS idx_pres_typing_expires ON pres_typing(expires_at);
"""


def create_tables(db):
    """Create all presence tables."""
    statements = [s.strip() for s in SCHEMA.split(";") if s.strip()]
    
    for statement in statements:
        if statement:
            try:
                converted = db.convert_schema(statement) if hasattr(db, 'convert_schema') else statement
                db.execute(converted)
            except Exception as e:
                logger.error(f"Failed to execute schema statement: {e}")
                raise
    
    logger.info("Presence tables created successfully")
