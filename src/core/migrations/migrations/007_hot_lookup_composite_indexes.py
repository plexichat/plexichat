"""
Migration 007: composite indexes for the hottest lookup paths.

The schema defines single-column indexes for these tables, but the most
frequent reads filter on one column and order/filter on a second:

* the admin audit feed filters by ``server_id`` and pages newest-first;
* message-author history filters by ``author_id`` and pages newest-first;
* the server events list filters by ``(server_id, status)`` and orders by
  ``start_time``.

With only single-column indexes the planner must fetch matching rows and then
build a temporary B-tree to sort them (``USE TEMP B-TREE FOR ORDER BY``). A
composite index whose leading column is the equality filter and whose trailing
column is the sort key serves the whole query in one index scan. All three
tables are large, append-heavy tables where this shows up on every request.

``CREATE INDEX IF NOT EXISTS`` keeps the migration idempotent on SQLite and
PostgreSQL.
"""

import logging

logger = logging.getLogger(__name__)


INDEXES = (
    ("idx_srv_audit_server_created", "srv_audit_log(server_id, created_at)"),
    ("idx_msg_messages_author_created", "msg_messages(author_id, created_at)"),
    (
        "idx_srv_events_server_status_start",
        "srv_scheduled_events(server_id, status, start_time)",
    ),
)


def up(db):
    """Create the composite lookup indexes (idempotent on both engines)."""
    for name, columns in INDEXES:
        db.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {columns}")
    logger.info("Migration 007: created %d composite lookup indexes", len(INDEXES))


def down(db):
    """Drop only the indexes this migration created."""
    for name, _columns in INDEXES:
        try:
            db.execute(f"DROP INDEX IF EXISTS {name}")
        except Exception:  # pragma: no cover - defensive, index may be gone
            pass
