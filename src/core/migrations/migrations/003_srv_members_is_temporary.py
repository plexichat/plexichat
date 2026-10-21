"""
Migration 003: srv_members.is_temporary column for temporary-invite membership.

``is_temporary`` was introduced in the servers module schema (Agent 16 F9)
after migration 000 had already been recorded as applied on existing
installations. Migration 000 never re-runs, and the additive ALTER tucked
inside ``create_servers_tables`` only executes for fresh installs, so
long-lived databases never received the column. Every server join
(``add_member`` inserts ``is_temporary`` explicitly) then failed with
``table srv_members has no column named is_temporary``, which stranded
invite joins with a 500 and cascaded into membership-dependent failures
(reports, thread join/add, server leave/transfer, artifact WS subscribe).

This migration applies that additive column change idempotently: fresh
installs already have the column (the module schema is authoritative) and
hit the expected duplicate-column path, while legacy databases get the
column added with the correct NOT NULL DEFAULT 0 definition. No backfill
is needed: no legacy row could have been created with a temporary flag
(the same broken INSERT path created all memberships), so 0 (permanent)
is the correct value for every pre-existing member.
"""

import logging

logger = logging.getLogger(__name__)


def _is_duplicate_column_error(exc: Exception) -> bool:
    """Whether *exc* reports that the column already exists.

    SQLite says "duplicate column name: is_temporary"; Postgres raises
    SQLSTATE 42701 with ``column "is_temporary" of relation "srv_members"
    already exists``. Matching only the SQLite wording made this migration
    raise on every fresh Postgres install, where migration 000's servers
    schema had already created the column.
    """
    message = str(exc).lower()
    if "duplicate column" in message:
        return True
    return "42701" in message or ("already exists" in message and "column" in message)


def up(db):
    """Add the is_temporary column to srv_members."""
    try:
        db.execute(
            "ALTER TABLE srv_members ADD COLUMN is_temporary INTEGER NOT NULL DEFAULT 0"
        )
        logger.info("Migration 003: added srv_members.is_temporary column")
    except Exception as e:  # pragma: no cover - defensive
        # Fresh installs create the table with is_temporary already present
        # (the module schema is authoritative); a duplicate-column error is
        # then expected.
        if not _is_duplicate_column_error(e):
            raise
        logger.info(
            "Migration 003: srv_members.is_temporary already present; skipping ALTER"
        )


def down(db):
    """Drop the is_temporary column (reversible; pre-existing rows keep defaults)."""
    try:
        db.execute("ALTER TABLE srv_members DROP COLUMN is_temporary")
    except Exception as e:  # pragma: no cover - defensive
        lowered = str(e).lower()
        if (
            "no such column" in lowered
            or "duplicate column" in lowered
            or ("column" in lowered and "does not exist" in lowered)
        ):
            return
        raise
