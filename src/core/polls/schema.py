"""
Poll database schema - Table definitions for polls module.
"""

import utils.logger as logger

from src.core.database.core.schema_splitter import split_sql_statements


SCHEMA = """
-- Polls table
CREATE TABLE IF NOT EXISTS poll_polls (
    id INTEGER PRIMARY KEY,
    message_id INTEGER NOT NULL UNIQUE,
    question TEXT NOT NULL,
    question_encrypted TEXT,
    created_by INTEGER NOT NULL,
    created_at INTEGER NOT NULL,
    ends_at INTEGER,
    ended_at INTEGER,
    allow_multiple_choice INTEGER NOT NULL DEFAULT 0,
    results_visibility TEXT NOT NULL DEFAULT 'always'
);

-- Polls indexes
CREATE INDEX IF NOT EXISTS idx_poll_polls_message ON poll_polls(message_id);
CREATE INDEX IF NOT EXISTS idx_poll_polls_creator ON poll_polls(created_by);
CREATE INDEX IF NOT EXISTS idx_poll_polls_ends ON poll_polls(ends_at);

-- Poll options table
CREATE TABLE IF NOT EXISTS poll_options (
    id INTEGER PRIMARY KEY,
    poll_id INTEGER NOT NULL,
    text TEXT NOT NULL,
    text_encrypted TEXT,
    position INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (poll_id) REFERENCES poll_polls(id) ON DELETE CASCADE
);

-- Poll options indexes
CREATE INDEX IF NOT EXISTS idx_poll_options_poll ON poll_options(poll_id);

-- Poll votes table
CREATE TABLE IF NOT EXISTS poll_votes (
    id INTEGER PRIMARY KEY,
    poll_id INTEGER NOT NULL,
    option_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    voted_at INTEGER NOT NULL,
    FOREIGN KEY (poll_id) REFERENCES poll_polls(id) ON DELETE CASCADE,
    FOREIGN KEY (option_id) REFERENCES poll_options(id) ON DELETE CASCADE,
    UNIQUE(poll_id, option_id, user_id)
);

-- Poll votes indexes
CREATE INDEX IF NOT EXISTS idx_poll_votes_poll ON poll_votes(poll_id);
CREATE INDEX IF NOT EXISTS idx_poll_votes_user ON poll_votes(user_id);
CREATE INDEX IF NOT EXISTS idx_poll_votes_option ON poll_votes(option_id);

-- Poll voter registry: exactly one row per (poll_id, user_id).  This
-- provides the authoritative, database-enforced "one vote per user per
-- poll" guard that a plain per-option ``poll_votes`` row (which can be
-- one-per-option for multiple-choice polls) cannot express.  The
-- ``UNIQUE(poll_id, user_id)`` constraint closes the inter-thread race
-- window that an application-level pre-INSERT check alone cannot.
CREATE TABLE IF NOT EXISTS poll_voters (
    poll_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    voted_at INTEGER NOT NULL,
    PRIMARY KEY (poll_id, user_id),
    FOREIGN KEY (poll_id) REFERENCES poll_polls(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_poll_voters_poll ON poll_voters(poll_id);
CREATE INDEX IF NOT EXISTS idx_poll_voters_user ON poll_voters(user_id);
"""


def create_tables(db):
    """Create all poll tables."""
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

    logger.info("Poll tables created successfully")
