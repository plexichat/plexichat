"""
PlexiJoin Federation module - Table definitions for federation system.
"""

import utils.logger as logger

from src.core.database.core.schema_splitter import split_sql_statements
from src.core.security.hardening import require_identifier


SCHEMA = """
-- PlexiJoin Connections table
CREATE TABLE IF NOT EXISTS plexijoin_connections (
    id INTEGER PRIMARY KEY,
    remote_instance_id TEXT NOT NULL UNIQUE,
    remote_url TEXT NOT NULL,
    shared_key_encrypted TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    connected_at INTEGER,
    messages_in INTEGER DEFAULT 0,
    messages_out INTEGER DEFAULT 0,
    last_activity INTEGER,
    note TEXT,
    created_at INTEGER NOT NULL,
    created_by INTEGER NOT NULL
);

-- PlexiJoin Connections indexes
CREATE INDEX IF NOT EXISTS idx_plexijoin_connections_status ON plexijoin_connections(status);
CREATE INDEX IF NOT EXISTS idx_plexijoin_connections_created ON plexijoin_connections(created_at);
-- Composite index for the admin list page: ``WHERE status = ? ORDER BY
-- created_at DESC`` and the matching COUNT can both be served from one index
-- range instead of sorting the whole table.
CREATE INDEX IF NOT EXISTS idx_plexijoin_connections_status_created ON plexijoin_connections(status, created_at);
-- Keyset (cursor) path for the hot admin list. ``id`` is appended as a
-- deterministic tiebreak so ``(created_at, id) < (?, ?)`` can seek straight to
-- a deep page in O(page size) instead of walking the whole OFFSET. One index
-- for the unfiltered list and one for the status-filtered list.
CREATE INDEX IF NOT EXISTS idx_plexijoin_connections_created_id ON plexijoin_connections(created_at, id);
CREATE INDEX IF NOT EXISTS idx_plexijoin_connections_status_created_id ON plexijoin_connections(status, created_at, id);
CREATE INDEX IF NOT EXISTS idx_plexijoin_connections_last_activity ON plexijoin_connections(last_activity);

-- PlexiJoin Inbound Requests table
CREATE TABLE IF NOT EXISTS plexijoin_inbound_requests (
    id INTEGER PRIMARY KEY,
    remote_instance_id TEXT NOT NULL,
    remote_url TEXT NOT NULL,
    requested_by TEXT,
    note TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    requested_at INTEGER NOT NULL,
    reviewed_at INTEGER,
    reviewed_by INTEGER
);

-- PlexiJoin Inbound Requests indexes
CREATE INDEX IF NOT EXISTS idx_plexijoin_inbound_status ON plexijoin_inbound_requests(status);
CREATE INDEX IF NOT EXISTS idx_plexijoin_inbound_requested ON plexijoin_inbound_requests(requested_at);

-- PlexiJoin Traffic Log table
CREATE TABLE IF NOT EXISTS plexijoin_traffic_log (
    id INTEGER PRIMARY KEY,
    connection_id INTEGER NOT NULL,
    direction TEXT NOT NULL,
    message_count INTEGER NOT NULL,
    recorded_at INTEGER NOT NULL,
    FOREIGN KEY (connection_id) REFERENCES plexijoin_connections(id) ON DELETE CASCADE
);

-- PlexiJoin Traffic Log indexes
CREATE INDEX IF NOT EXISTS idx_plexijoin_traffic_connection ON plexijoin_traffic_log(connection_id);
CREATE INDEX IF NOT EXISTS idx_plexijoin_traffic_recorded ON plexijoin_traffic_log(recorded_at);
CREATE INDEX IF NOT EXISTS idx_plexijoin_traffic_direction ON plexijoin_traffic_log(direction);

-- Single-use inbound federation request nonces. The composite key binds a
-- nonce to the connection whose shared key authenticated it.
CREATE TABLE IF NOT EXISTS plexijoin_replay_nonces (
    connection_id INTEGER NOT NULL,
    nonce TEXT NOT NULL,
    seen_at INTEGER NOT NULL,
    PRIMARY KEY (connection_id, nonce),
    FOREIGN KEY (connection_id) REFERENCES plexijoin_connections(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_plexijoin_replay_seen ON plexijoin_replay_nonces(seen_at);
"""


def create_tables(db):
    """Create all PlexiJoin tables."""
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

    logger.info("PlexiJoin tables created successfully")


def drop_tables(db):
    """Drop all PlexiJoin tables. USE WITH CAUTION."""
    indexes = [
        "idx_plexijoin_connections_status",
        "idx_plexijoin_connections_created",
        "idx_plexijoin_connections_status_created",
        "idx_plexijoin_connections_created_id",
        "idx_plexijoin_connections_status_created_id",
        "idx_plexijoin_connections_last_activity",
        "idx_plexijoin_inbound_status",
        "idx_plexijoin_inbound_requested",
        "idx_plexijoin_traffic_connection",
        "idx_plexijoin_traffic_recorded",
        "idx_plexijoin_traffic_direction",
        "idx_plexijoin_replay_seen",
    ]
    for index in indexes:
        db.execute(f"DROP INDEX IF EXISTS {require_identifier(index, 'index name')}")

    tables = [
        "plexijoin_traffic_log",
        "plexijoin_inbound_requests",
        "plexijoin_connections",
        "plexijoin_replay_nonces",
    ]
    for table in tables:
        db.execute(f"DROP TABLE IF EXISTS {require_identifier(table, 'table name')}")
