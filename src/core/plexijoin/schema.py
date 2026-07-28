"""
PlexiJoin Federation module - Table definitions for federation system.
"""

import utils.logger as logger

from src.core.database.core.schema_splitter import split_sql_statements


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
        "idx_plexijoin_connections_last_activity",
        "idx_plexijoin_inbound_status",
        "idx_plexijoin_inbound_requested",
        "idx_plexijoin_traffic_connection",
        "idx_plexijoin_traffic_recorded",
        "idx_plexijoin_traffic_direction",
    ]
    for index in indexes:
        db.execute(f"DROP INDEX IF EXISTS {index}")

    tables = [
        "plexijoin_traffic_log",
        "plexijoin_inbound_requests",
        "plexijoin_connections",
    ]
    for table in tables:
        db.execute(f"DROP TABLE IF EXISTS {table}")
