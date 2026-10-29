"""
Migration 005: make admin_approvals.approved_by a text column.

The approval vote list has been stored as a JSON array of admin ids in
``approved_by`` since the multi-admin approval flow landed, but the canonical
schema declared the column ``INTEGER``. SQLite never enforced that (it stores
whatever it is given), so the code kept working there; PostgreSQL typed the
column ``BIGINT`` and rejected every vote with ``invalid input syntax for type
bigint: '[12,45]'``, which surfaced as a 500 on
``POST /api/v1/admin/approvals/{id}/approve`` and left every approval-gated
admin action (migrations, reindex, emergency override) unapprovable.

``src/core/admin/schema.py`` now declares the column ``TEXT`` so new installs
are correct; this migration repairs existing databases by widening the column.
Rows written by the old code hold a bare admin id, which casts cleanly, and
``_parse_approved_by`` already reads bare integers, CSV lists and JSON arrays.
"""

import logging

logger = logging.getLogger(__name__)

_INTEGER_TYPES = ("bigint", "integer", "smallint")


def _approved_by_type(db):
    """Return the column's current type, or None when it cannot be read."""
    row = db.fetch_one(
        "SELECT data_type FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = 'admin_approvals' "
        "AND column_name = 'approved_by'"
    )
    if not row:
        return None
    return row["data_type"] if isinstance(row, dict) else row[0]


def up(db):
    """Widen ``approved_by`` from an integer to text on PostgreSQL."""
    if db.type != "postgres":
        # SQLite is dynamically typed: the existing column already stores the
        # JSON list, and the declaration only affects newly created tables.
        return
    try:
        column_type = _approved_by_type(db)
    except Exception as e:  # pragma: no cover - defensive
        logger.warning("Migration 005: could not read approved_by type: %s", e)
        return
    if column_type is None:
        return
    if str(column_type).lower() not in _INTEGER_TYPES:
        logger.info("Migration 005: admin_approvals.approved_by is already text")
        return
    db.execute(
        "ALTER TABLE admin_approvals ALTER COLUMN approved_by TYPE TEXT "
        "USING approved_by::text"
    )
    logger.info("Migration 005: widened admin_approvals.approved_by to text")


def down(db):
    """No-op: narrowing back to an integer would discard the vote list."""
    logger.info("Migration 005 rollback: approved_by stays text")
