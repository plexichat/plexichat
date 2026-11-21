"""
Migration 014: composite indexes for the hot webhook listing paths.

The webhook management panel lists a channel's or a server's webhooks
oldest-first:

    SELECT * FROM webhook_webhooks WHERE channel_id = ? ORDER BY created_at
    SELECT * FROM webhook_webhooks WHERE server_id = ? ORDER BY created_at

The schema only has single-column indexes on ``channel_id`` and ``server_id``.
With those, the planner seeks the rows through the equality index and then
materialises a temporary B-tree to order them (``USE TEMP B-TREE FOR ORDER
BY``) on every webhook settings view. A composite index whose leading column is
the equality filter and whose trailing column is the sort key serves the whole
query in one ordered scan.

This definition mirrors the authoritative one in the webhooks schema so fresh
installs and already-migrated databases converge on the same shape.
``CREATE INDEX IF NOT EXISTS`` keeps the migration idempotent on SQLite and
PostgreSQL.
"""

import logging

from src.core.security.hardening import require_identifier

logger = logging.getLogger(__name__)


# (index name, table name, ordered columns). All entries are static literals;
# the identifiers are still passed through require_identifier at the point of
# use so the DDL identifier guard audit does not need an allowlist exception.
INDEXES = (
    (
        "idx_webhook_channel_created",
        "webhook_webhooks",
        ("channel_id", "created_at"),
    ),
    (
        "idx_webhook_server_created",
        "webhook_webhooks",
        ("server_id", "created_at"),
    ),
)


def up(db):
    """Create the composite webhook listing indexes (idempotent on both engines)."""
    for name, table, columns in INDEXES:
        db.execute(
            f"CREATE INDEX IF NOT EXISTS {require_identifier(name, 'index name')} "
            f"ON {require_identifier(table, 'table name')} "
            f"({', '.join(require_identifier(col, 'column name') for col in columns)})"
        )
    logger.info("Migration 014: created %d webhook listing indexes", len(INDEXES))


def down(db):
    """Drop only the indexes this migration created."""
    for name, _table, _columns in INDEXES:
        try:
            db.execute(f"DROP INDEX IF EXISTS {require_identifier(name, 'index name')}")
        except Exception:  # pragma: no cover - defensive, index may be gone
            pass
