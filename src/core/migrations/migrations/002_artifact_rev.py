"""
Migration 002: artifacts.rev column for optimistic concurrency.

The code/text editor used to carry its monotonic revision inside
``payload["rev"]`` and the PATCH route compared the two payloads to reject
stale writes (409). That comparison is a read-then-write and can race. This
migration adds a dedicated ``rev`` column so the guard can be enforced with
an atomic ``UPDATE ... WHERE id = ? AND rev = ?``.

The column is seeded from the existing ``payload.rev`` chain so already-edited
artifacts keep counting from where they left off; artifacts without a numeric
payload rev start at 0 (their first editor write becomes rev 1).
"""

import json
import logging

logger = logging.getLogger(__name__)


def _is_duplicate_column_error(exc: Exception) -> bool:
    """Whether *exc* reports that the column already exists.

    SQLite says "duplicate column name: rev"; Postgres raises SQLSTATE 42701
    with ``column "rev" of relation "artifacts" already exists``. Matching
    only the SQLite wording made this migration fail on every fresh Postgres
    install, where migration 000's module schema had already created the
    column -- which then aborted the whole migration run.
    """
    message = str(exc).lower()
    if "duplicate column" in message:
        return True
    return "42701" in message or ("already exists" in message and "column" in message)


def _payload_rev(payload: object):
    """Return a numeric ``rev`` from a legacy payload string, or None."""
    if not isinstance(payload, str):
        return None
    try:
        parsed = json.loads(payload)
    except (ValueError, TypeError):
        return None
    if not isinstance(parsed, dict):
        return None
    raw = parsed.get("rev")
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    try:
        rev = int(raw)
    except (TypeError, ValueError):
        return None
    return rev if rev > 0 else None


def up(db):
    """Add the rev column and backfill it from payload.rev."""
    try:
        db.execute("ALTER TABLE artifacts ADD COLUMN rev INTEGER NOT NULL DEFAULT 0")
        logger.info("Migration 002: added artifacts.rev column")
    except Exception as e:  # pragma: no cover - defensive
        # Fresh installs create the table with rev already present (the module
        # schema is authoritative); a duplicate-column error is then expected.
        if not _is_duplicate_column_error(e):
            raise
        logger.info("Migration 002: artifacts.rev already present; skipping ALTER")

    # Backfill: seed the column from the legacy payload.rev chain so existing
    # editors continue from their last revision instead of restarting at 0.
    try:
        rows = db.fetch_all("SELECT id, payload FROM artifacts")
    except Exception:  # pragma: no cover - defensive
        rows = None
    updated = 0
    for row in rows or []:
        rev = _payload_rev(row.get("payload"))
        if rev is None:
            continue
        db.execute("UPDATE artifacts SET rev = ? WHERE id = ?", (rev, row["id"]))
        updated += 1
    if updated:
        logger.info("Migration 002: backfilled rev for %d artifact(s)", updated)


def down(db):
    """Drop the rev column (reversible; backfilled data is not re-encoded)."""
    try:
        db.execute("ALTER TABLE artifacts DROP COLUMN rev")
    except Exception as e:  # pragma: no cover - defensive
        lowered = str(e).lower()
        if (
            "no such column" in lowered
            or "duplicate column" in lowered
            or ("column" in lowered and "does not exist" in lowered)
        ):
            return
        raise
