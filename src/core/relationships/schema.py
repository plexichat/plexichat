"""
Relationship database schema - Table definitions for relationships module.
"""

import utils.logger as logger

from src.core.database.core.schema_splitter import split_sql_statements


SCHEMA = """
-- Friends table (bidirectional friendship)
CREATE TABLE IF NOT EXISTS rel_friends (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL,
    friend_id INTEGER NOT NULL,
    created_at INTEGER NOT NULL,
    UNIQUE(user_id, friend_id),
    FOREIGN KEY (user_id) REFERENCES auth_users(id) ON DELETE CASCADE,
    FOREIGN KEY (friend_id) REFERENCES auth_users(id) ON DELETE CASCADE
);

-- Friends indexes
CREATE INDEX IF NOT EXISTS idx_rel_friends_user ON rel_friends(user_id);
CREATE INDEX IF NOT EXISTS idx_rel_friends_friend ON rel_friends(friend_id);
-- The friends list filters by user_id and pages newest-first. The single-column
-- index finds the rows but forces a temporary sort B-tree; this composite
-- serves the ordered scan directly. Migration 009 adds it to databases
-- created before the index was defined.
CREATE INDEX IF NOT EXISTS idx_rel_friends_user_created ON rel_friends(user_id, created_at);

-- Friend requests table
CREATE TABLE IF NOT EXISTS rel_friend_requests (
    id INTEGER PRIMARY KEY,
    sender_id INTEGER NOT NULL,
    recipient_id INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    message TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    UNIQUE(sender_id, recipient_id),
    FOREIGN KEY (sender_id) REFERENCES auth_users(id) ON DELETE CASCADE,
    FOREIGN KEY (recipient_id) REFERENCES auth_users(id) ON DELETE CASCADE
);

-- Friend requests indexes
CREATE INDEX IF NOT EXISTS idx_rel_requests_sender ON rel_friend_requests(sender_id);
CREATE INDEX IF NOT EXISTS idx_rel_requests_recipient ON rel_friend_requests(recipient_id);
-- Incoming/outgoing pending-request lists filter by (recipient|sender, status)
-- and page newest-first. The single-column indexes find the rows but force a
-- temporary sort B-tree; these composites serve the ordered scans directly.
-- Migration 009 adds them to databases created before the indexes were defined.
CREATE INDEX IF NOT EXISTS idx_rel_requests_recipient_status_created ON rel_friend_requests(recipient_id, status, created_at);
CREATE INDEX IF NOT EXISTS idx_rel_requests_sender_status_created ON rel_friend_requests(sender_id, status, created_at);
-- Note: idx_rel_requests_status removed - low cardinality (pending/accepted/declined), rarely queried alone

-- Blocked users table
CREATE TABLE IF NOT EXISTS rel_blocked (
    id INTEGER PRIMARY KEY,
    blocker_id INTEGER NOT NULL,
    blocked_id INTEGER NOT NULL,
    reason TEXT,
    created_at INTEGER NOT NULL,
    UNIQUE(blocker_id, blocked_id),
    FOREIGN KEY (blocker_id) REFERENCES auth_users(id) ON DELETE CASCADE,
    FOREIGN KEY (blocked_id) REFERENCES auth_users(id) ON DELETE CASCADE
);

-- Blocked indexes
CREATE INDEX IF NOT EXISTS idx_rel_blocked_blocker ON rel_blocked(blocker_id);
CREATE INDEX IF NOT EXISTS idx_rel_blocked_blocked ON rel_blocked(blocked_id);
"""


def create_tables(db):
    """Create all relationship tables."""
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

    logger.info("Relationship tables created successfully")
