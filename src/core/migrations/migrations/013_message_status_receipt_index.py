"""
Migration 013: composite index for the hot message-status read-receipt list.

The message status panel lists every receipt for one message newest-first:

    SELECT * FROM msg_message_status WHERE message_id = ? ORDER BY timestamp

and the reader-avatar paths filter the same rows further:

    SELECT user_id FROM msg_message_status
    WHERE message_id = ? AND status = 'read' ORDER BY timestamp ASC

The schema only defines single-column indexes on ``message_id`` and ``user_id``
(the ``UNIQUE(message_id, user_id)`` constraint also only leads with
``message_id``). With those, the planner scans the rows and materialises a
temporary B-tree to order them (``USE TEMP B-TREE FOR ORDER BY``) on every
status view. A composite index whose leading column is the equality filter and
whose trailing column is the sort key serves both reads in one ordered scan;
the status predicate is applied as a residual filter without disturbing the
timestamp order.

This definition mirrors the authoritative one in the messaging schema so fresh
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
        "idx_msg_status_message_timestamp",
        "msg_message_status",
        ("message_id", "timestamp"),
    ),
)


def up(db):
    """Create the composite message-status index (idempotent on both engines)."""
    for name, table, columns in INDEXES:
        db.execute(
            f"CREATE INDEX IF NOT EXISTS {require_identifier(name, 'index name')} "
            f"ON {require_identifier(table, 'table name')} "
            f"({', '.join(require_identifier(col, 'column name') for col in columns)})"
        )
    logger.info("Migration 013: created %d message-status index(es)", len(INDEXES))


def down(db):
    """Drop only the index this migration created."""
    for name, _table, _columns in INDEXES:
        try:
            db.execute(f"DROP INDEX IF EXISTS {require_identifier(name, 'index name')}")
        except Exception:  # pragma: no cover - defensive, index may be gone
            pass
