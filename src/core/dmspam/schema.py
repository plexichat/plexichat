"""
DM spam database schema - Table definitions for DM anti-spam module.
"""

import utils.logger as logger

from src.core.database.core.schema_splitter import split_sql_statements


SCHEMA = """
-- DM spam filters table
CREATE TABLE IF NOT EXISTS dm_spam_filters (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL,
    target_user_id INTEGER,
    pattern TEXT,
    filter_type TEXT NOT NULL DEFAULT 'rate',
    action TEXT NOT NULL DEFAULT 'warn',
    threshold INTEGER NOT NULL DEFAULT 5,
    window_seconds INTEGER NOT NULL DEFAULT 60,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

-- DM spam filters indexes
CREATE INDEX IF NOT EXISTS idx_dm_spam_user ON dm_spam_filters(user_id);
CREATE INDEX IF NOT EXISTS idx_dm_spam_target ON dm_spam_filters(target_user_id);

-- DM spam events table
CREATE TABLE IF NOT EXISTS dm_spam_events (
    id INTEGER PRIMARY KEY,
    sender_id INTEGER NOT NULL,
    recipient_id INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    content_hash TEXT,
    created_at INTEGER NOT NULL
);

-- DM spam events indexes
CREATE INDEX IF NOT EXISTS idx_dm_spam_evt_sender ON dm_spam_events(sender_id);
CREATE INDEX IF NOT EXISTS idx_dm_spam_evt_created ON dm_spam_events(created_at);
CREATE INDEX IF NOT EXISTS idx_dm_spam_evt_recipient_created ON dm_spam_events(recipient_id, created_at);

-- Shared rate/duplicate counters. Valkey is the primary counter store, but
-- when it is unreachable every worker must still observe the SAME counter:
-- a per-process dict would enforce the DM rate/duplicate limits N times over
-- under --scale backend=N. A row is one (key, fixed-window) bucket; rows are
-- swept by window age and hard-capped, so the table stays bounded.
CREATE TABLE IF NOT EXISTS dm_spam_counters (
    id INTEGER PRIMARY KEY,
    cache_key TEXT NOT NULL,
    window_start INTEGER NOT NULL,
    count INTEGER NOT NULL DEFAULT 0,
    UNIQUE (cache_key, window_start)
);

-- Window-age sweep and oldest-first cap queries both order/filter here.
CREATE INDEX IF NOT EXISTS idx_dm_spam_counter_window ON dm_spam_counters(window_start);
"""


def create_dmspam_tables(db):
    """Create all DM spam tables."""
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

    logger.info("DM spam tables created successfully")
