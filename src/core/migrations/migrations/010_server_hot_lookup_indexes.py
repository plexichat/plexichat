"""
Migration 010: composite indexes for hot server lookup paths.

Two server reads filter on an equality column and page by a sort key, but the
schema only has single-column indexes on the filter columns:

* the scheduled-event list for a server is read without a status filter and
  paged by ``start_time``. The composite index from migration 007 has
  ``status`` as its middle column, so it cannot provide an ordered
  ``start_time`` when status is unconstrained and the planner temp-sorts;
* the RSVP list for an event is paged oldest-first by ``created_at``, and only
  a single-column ``event_id`` index exists.

With those access paths the planner finds the matching rows and then
materialises a temporary B-tree to order them (``USE TEMP B-TREE FOR ORDER
BY``). A composite index whose leading column is the equality filter and whose
trailing column is the sort key serves the whole query in one ordered scan.
These definitions mirror the authoritative ones in the server schema so fresh
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
        "idx_srv_events_server_start",
        "srv_scheduled_events",
        ("server_id", "start_time"),
    ),
    (
        "idx_srv_event_rsvps_event_created",
        "srv_event_rsvps",
        ("event_id", "created_at"),
    ),
)


def up(db):
    """Create the composite server lookup indexes (idempotent on both engines)."""
    for name, table, columns in INDEXES:
        db.execute(
            f"CREATE INDEX IF NOT EXISTS {require_identifier(name, 'index name')} "
            f"ON {require_identifier(table, 'table name')} "
            f"({', '.join(require_identifier(col, 'column name') for col in columns)})"
        )
    logger.info(
        "Migration 010: created %d composite server lookup indexes", len(INDEXES)
    )


def down(db):
    """Drop only the indexes this migration created."""
    for name, _table, _columns in INDEXES:
        try:
            db.execute(f"DROP INDEX IF EXISTS {require_identifier(name, 'index name')}")
        except Exception:  # pragma: no cover - defensive, index may be gone
            pass
