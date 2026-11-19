"""
Migration 011: composite index for the hot admin-audit action feed.

The cross-worker tuning audit feed reads the same ``admin_audit_log`` as the
``/audit`` endpoints with a fixed ``action`` and pages newest-first:

    SELECT ... FROM admin_audit_log a
    WHERE a.action = 'cross_worker_tuning_update'
    ORDER BY a.created_at DESC LIMIT ?

The schema only has single-column indexes on ``action`` and ``created_at``.
With those, the planner finds the matching rows through
``idx_admin_audit_action`` and then materialises a temporary B-tree to order
them (``USE TEMP B-TREE FOR ORDER BY``) on every request. A composite index
whose leading column is the equality filter and whose trailing column is the
sort key serves the whole query in one ordered scan (SQLite walks the index
backwards for the ``DESC`` ordering).

This definition mirrors the authoritative one in the admin schema so fresh
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
        "idx_admin_audit_action_created",
        "admin_audit_log",
        ("action", "created_at"),
    ),
)


def up(db):
    """Create the composite admin-audit index (idempotent on both engines)."""
    for name, table, columns in INDEXES:
        db.execute(
            f"CREATE INDEX IF NOT EXISTS {require_identifier(name, 'index name')} "
            f"ON {require_identifier(table, 'table name')} "
            f"({', '.join(require_identifier(col, 'column name') for col in columns)})"
        )
    logger.info("Migration 011: created %d admin audit index(es)", len(INDEXES))


def down(db):
    """Drop only the index this migration created."""
    for name, _table, _columns in INDEXES:
        try:
            db.execute(f"DROP INDEX IF EXISTS {require_identifier(name, 'index name')}")
        except Exception:  # pragma: no cover - defensive, index may be gone
            pass
