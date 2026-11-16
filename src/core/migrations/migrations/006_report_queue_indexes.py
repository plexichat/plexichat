"""
Migration 006: composite indexes for the report moderation queues.

The moderator views filter by status and order by reported_at, and the
idempotent submission guards look up a (reporter, target, status) triple.
The single-column indexes cannot serve either access path efficiently, so
this migration adds the composite indexes. CREATE INDEX IF NOT EXISTS keeps
it idempotent on both SQLite and PostgreSQL.
"""

import logging

logger = logging.getLogger(__name__)


INDEXES = (
    (
        "idx_message_reports_status_reported_at",
        "message_reports(status, reported_at)",
    ),
    (
        "idx_user_reports_status_reported_at",
        "user_reports(status, reported_at)",
    ),
    (
        "idx_message_reports_reporter_message_status",
        "message_reports(reporter_id, message_id, status)",
    ),
    (
        "idx_user_reports_reporter_reported_status",
        "user_reports(reporter_id, reported_user_id, status)",
    ),
)


def up(db):
    """Create the composite indexes (idempotent on both engines)."""
    for name, columns in INDEXES:
        db.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {columns}")
    logger.info("Migration 006: created %d composite report indexes", len(INDEXES))


def down(db):
    """Drop only the indexes this migration created."""
    for name, _columns in INDEXES:
        try:
            db.execute(f"DROP INDEX IF EXISTS {name}")
        except Exception:  # pragma: no cover - defensive, index may be gone
            pass
