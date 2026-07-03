"""
PlexiJoin Federation System Migration.

Adds:
- plexijoin_connections: Outbound federation links
- plexijoin_inbound_requests: Incoming join requests
- plexijoin_traffic_log: Message traffic counters
- Indexes for admin audit query performance
"""

import logging

logger = logging.getLogger(__name__)


def up(db):
    """Apply the migration."""
    logger.info("Migration 035: Starting PlexiJoin Federation System")

    # === PlexiJoin Connections Table ===
    db.execute("""
        CREATE TABLE IF NOT EXISTS plexijoin_connections (
            id BIGINT PRIMARY KEY,
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
            created_by BIGINT NOT NULL
        )
    """)
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_plexijoin_connections_status ON plexijoin_connections(status)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_plexijoin_connections_created ON plexijoin_connections(created_at)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_plexijoin_connections_last_activity ON plexijoin_connections(last_activity)"
    )

    # === PlexiJoin Inbound Requests Table ===
    db.execute("""
        CREATE TABLE IF NOT EXISTS plexijoin_inbound_requests (
            id BIGINT PRIMARY KEY,
            remote_instance_id TEXT NOT NULL,
            remote_url TEXT NOT NULL,
            requested_by TEXT,
            note TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            requested_at INTEGER NOT NULL,
            reviewed_at INTEGER,
            reviewed_by BIGINT
        )
    """)
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_plexijoin_inbound_status ON plexijoin_inbound_requests(status)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_plexijoin_inbound_requested ON plexijoin_inbound_requests(requested_at)"
    )

    # === PlexiJoin Traffic Log Table ===
    db.execute("""
        CREATE TABLE IF NOT EXISTS plexijoin_traffic_log (
            id BIGINT PRIMARY KEY,
            connection_id BIGINT NOT NULL,
            direction TEXT NOT NULL,
            message_count INTEGER NOT NULL,
            recorded_at INTEGER NOT NULL,
            FOREIGN KEY (connection_id) REFERENCES plexijoin_connections(id) ON DELETE CASCADE
        )
    """)
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_plexijoin_traffic_connection ON plexijoin_traffic_log(connection_id)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_plexijoin_traffic_recorded ON plexijoin_traffic_log(recorded_at)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_plexijoin_traffic_direction ON plexijoin_traffic_log(direction)"
    )

    # === Admin Audit Performance Indexes ===
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_admin_audit_action_status ON admin_audit_log(action, status)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_admin_audit_admin_created ON admin_audit_log(admin_id, created_at)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_admin_audit_target_type ON admin_audit_log(target_type)"
    )

    logger.info("Migration 035: PlexiJoin Federation System completed")


def down(db):
    """Rollback the migration."""
    logger.info("Migration 035: Rolling back PlexiJoin Federation System")

    tables = [
        "plexijoin_traffic_log",
        "plexijoin_inbound_requests",
        "plexijoin_connections",
    ]
    for table in tables:
        if db.table_exists(table):
            db.execute(f"DROP TABLE IF EXISTS {table}")

    indexes = [
        "idx_plexijoin_connections_status",
        "idx_plexijoin_connections_created",
        "idx_plexijoin_connections_last_activity",
        "idx_plexijoin_inbound_status",
        "idx_plexijoin_inbound_requested",
        "idx_plexijoin_traffic_connection",
        "idx_plexijoin_traffic_recorded",
        "idx_plexijoin_traffic_direction",
        "idx_admin_audit_action_status",
        "idx_admin_audit_admin_created",
        "idx_admin_audit_target_type",
    ]
    for index in indexes:
        db.execute(f"DROP INDEX IF EXISTS {index}")

    logger.info("Migration 035: PlexiJoin Federation System rollback completed")
