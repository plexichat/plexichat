"""
Migration 012: covering index for the admin-audit stats counts.

``GET /api/v1/admin/audit/logs/stats`` issues eleven ``COUNT(*)`` queries against
``admin_audit_log``. Three filter ``created_at >= <UTC midnight>`` by status;
the other eight filter that same range by ``action LIKE '<family>%'``. With only
the single-column indexes the family counts seek the created_at range through
``idx_admin_audit_created`` and then look up every candidate row in the table to
evaluate the LIKE predicate. On a full retention window that is one table fetch
per audited event, multiplied by eight families on every stats request.

A composite ``(created_at, status, action)`` index makes those counts covering:
the planner seeks the created_at range and evaluates both the status predicate
and the action LIKE straight from the index, with no per-row table lookup. The
leading column stays created_at so the existing range scans keep their shape,
and status/action are appended so the date-bound counts remain index-only.

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
        "idx_admin_audit_created_status_action",
        "admin_audit_log",
        ("created_at", "status", "action"),
    ),
)


def up(db):
    """Create the covering admin-audit stats index (idempotent on both engines)."""
    for name, table, columns in INDEXES:
        db.execute(
            f"CREATE INDEX IF NOT EXISTS {require_identifier(name, 'index name')} "
            f"ON {require_identifier(table, 'table name')} "
            f"({', '.join(require_identifier(col, 'column name') for col in columns)})"
        )
    logger.info("Migration 012: created %d admin audit index(es)", len(INDEXES))


def down(db):
    """Drop only the index this migration created."""
    for name, _table, _columns in INDEXES:
        try:
            db.execute(f"DROP INDEX IF EXISTS {require_identifier(name, 'index name')}")
        except Exception:  # pragma: no cover - defensive, index may be gone
            pass
