"""
Add the server_artifact_settings table for per-server artifact retention overrides.

Stores a per-server retention override (in days) that, when present and when
``artifacts.allow_per_server_override`` is enabled, takes precedence over the
global ``artifacts.default_retention_days`` for artifacts owned by that server.

Version: 048
Depends: 047
"""

import logging

logger = logging.getLogger(__name__)


SCHEMA = """
-- Per-server artifact settings (retention override per server)
CREATE TABLE IF NOT EXISTS server_artifact_settings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    server_id INTEGER NOT NULL UNIQUE,
    retention_days INTEGER,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    FOREIGN KEY (server_id) REFERENCES srv_servers(id)
);

CREATE INDEX IF NOT EXISTS idx_server_artifact_settings_server
    ON server_artifact_settings(server_id);
"""


def up(db):
    """Apply the server_artifact_settings migration."""
    logger.info("Migration 048: Starting server_artifact_settings creation")

    statements = [s.strip() for s in SCHEMA.split(";") if s.strip()]
    for statement in statements:
        try:
            converted = (
                db.convert_schema(statement)
                if hasattr(db, "convert_schema")
                else statement
            )
            db.execute(converted)
        except Exception as e:
            logger.error(f"Migration 048: Failed to execute statement: {e}")
            raise

    logger.info("Migration 048 completed successfully")


def down(db):
    """Rollback the server_artifact_settings migration."""
    logger.info("Migration 048 rollback: dropping server_artifact_settings table")
    try:
        db.execute("DROP TABLE IF EXISTS server_artifact_settings")
        logger.info("Migration 048 rollback completed")
    except Exception as e:
        logger.warning(f"Migration 048 rollback: Failed to drop table: {e}")
