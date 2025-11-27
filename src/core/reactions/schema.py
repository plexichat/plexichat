"""
Reaction database schema - Table definitions for reactions module.
"""

import utils.logger as logger


SCHEMA = """
-- Reactions table
CREATE TABLE IF NOT EXISTS react_reactions (
    id INTEGER PRIMARY KEY,
    message_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    emoji TEXT NOT NULL,
    is_custom INTEGER NOT NULL DEFAULT 0,
    custom_emoji_id INTEGER,
    created_at INTEGER NOT NULL,
    UNIQUE(message_id, user_id, emoji)
);

-- Reactions indexes
CREATE INDEX IF NOT EXISTS idx_react_message ON react_reactions(message_id);
CREATE INDEX IF NOT EXISTS idx_react_user ON react_reactions(user_id);
CREATE INDEX IF NOT EXISTS idx_react_emoji ON react_reactions(message_id, emoji);

-- Custom emoji table (server-specific)
CREATE TABLE IF NOT EXISTS react_custom_emoji (
    id INTEGER PRIMARY KEY,
    server_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    animated INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL,
    UNIQUE(server_id, name)
);

-- Custom emoji indexes
CREATE INDEX IF NOT EXISTS idx_react_custom_server ON react_custom_emoji(server_id);
"""


def create_tables(db):
    """Create all reaction tables."""
    statements = [s.strip() for s in SCHEMA.split(";") if s.strip()]
    
    for statement in statements:
        if statement:
            try:
                db.execute(statement)
            except Exception as e:
                logger.error(f"Failed to execute schema statement: {e}")
                raise
    
    logger.info("Reaction tables created successfully")
