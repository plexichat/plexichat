"""
Migration 009: composite indexes for hot relationship lookup paths.

Three relationship list reads filter on a single equality column and page by
``created_at``; the schema only has single-column indexes on the filter columns:

* the friends list filters by ``user_id`` and orders newest-first;
* incoming pending requests filter by ``(recipient_id, status)`` and order
  newest-first;
* outgoing pending requests filter by ``(sender_id, status)`` and order
  newest-first.

With only the single-column indexes the planner finds the matching rows and
then materialises a temporary B-tree to order them (``USE TEMP B-TREE FOR ORDER
BY``). A composite index whose leading columns are the equality filters and
whose trailing column is the sort key serves the whole query in one ordered
scan (SQLite walks the index backwards for the ``DESC`` ordering). All three
reads run on relationship page loads, so this shows up on every session.

These index definitions mirror the authoritative ones in the relationship
schema so fresh installs and already-migrated databases converge on the same
shape. ``CREATE INDEX IF NOT EXISTS`` keeps the migration idempotent on SQLite
and PostgreSQL.
"""

import logging

from src.core.security.hardening import require_identifier

logger = logging.getLogger(__name__)


# (index name, table name, ordered columns). All entries are static literals;
# the identifiers are still passed through require_identifier at the point of
# use so the DDL identifier guard audit does not need an allowlist exception.
INDEXES = (
    (
        "idx_rel_friends_user_created",
        "rel_friends",
        ("user_id", "created_at"),
    ),
    (
        "idx_rel_requests_recipient_status_created",
        "rel_friend_requests",
        ("recipient_id", "status", "created_at"),
    ),
    (
        "idx_rel_requests_sender_status_created",
        "rel_friend_requests",
        ("sender_id", "status", "created_at"),
    ),
)


def up(db):
    """Create the composite relationship lookup indexes (idempotent on both engines)."""
    for name, table, columns in INDEXES:
        db.execute(
            f"CREATE INDEX IF NOT EXISTS {require_identifier(name, 'index name')} "
            f"ON {require_identifier(table, 'table name')} "
            f"({', '.join(require_identifier(col, 'column name') for col in columns)})"
        )
    logger.info("Migration 009: created %d composite relationship lookup indexes", len(INDEXES))


def down(db):
    """Drop only the indexes this migration created."""
    for name, _table, _columns in INDEXES:
        try:
            db.execute(
                f"DROP INDEX IF EXISTS {require_identifier(name, 'index name')}"
            )
        except Exception:  # pragma: no cover - defensive, index may be gone
            pass
