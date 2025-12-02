"""
Relationship database schema - Table definitions for relationships module.
"""

import utils.logger as logger


SCHEMA = """
-- Friends table (bidirectional friendship)
CREATE TABLE IF NOT EXISTS rel_friends (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL,
    friend_id INTEGER NOT NULL,
    created_at INTEGER NOT NULL,
    UNIQUE(user_id, friend_id)
);

-- Friends indexes
CREATE INDEX IF NOT EXISTS idx_rel_friends_user ON rel_friends(user_id);
CREATE INDEX IF NOT EXISTS idx_rel_friends_friend ON rel_friends(friend_id);

-- Friend requests table
CREATE TABLE IF NOT EXISTS rel_friend_requests (
    id INTEGER PRIMARY KEY,
    sender_id INTEGER NOT NULL,
    recipient_id INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    message TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    UNIQUE(sender_id, recipient_id)
);

-- Friend requests indexes
CREATE INDEX IF NOT EXISTS idx_rel_requests_sender ON rel_friend_requests(sender_id);
CREATE INDEX IF NOT EXISTS idx_rel_requests_recipient ON rel_friend_requests(recipient_id);
CREATE INDEX IF NOT EXISTS idx_rel_requests_status ON rel_friend_requests(status);

-- Blocked users table
CREATE TABLE IF NOT EXISTS rel_blocked (
    id INTEGER PRIMARY KEY,
    blocker_id INTEGER NOT NULL,
    blocked_id INTEGER NOT NULL,
    reason TEXT,
    created_at INTEGER NOT NULL,
    UNIQUE(blocker_id, blocked_id)
);

-- Blocked indexes
CREATE INDEX IF NOT EXISTS idx_rel_blocked_blocker ON rel_blocked(blocker_id);
CREATE INDEX IF NOT EXISTS idx_rel_blocked_blocked ON rel_blocked(blocked_id);
"""


def create_tables(db):
    """Create all relationship tables."""
    statements = [s.strip() for s in SCHEMA.split(";") if s.strip()]
    
    for statement in statements:
        if statement:
            try:
                converted = db.convert_schema(statement) if hasattr(db, 'convert_schema') else statement
                db.execute(converted)
            except Exception as e:
                logger.error(f"Failed to execute schema statement: {e}")
                raise
    
    logger.info("Relationship tables created successfully")
