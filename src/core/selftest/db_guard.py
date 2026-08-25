"""Disposable-database guard for self-test destructive phases.

Whole-table wipes and migration rollback tests destroy data by design.
They must only ever run against a database that has been explicitly
flagged as disposable. A database counts as disposable when ANY of:

* ``database.disposable: true`` in configuration (checked at DB init,
  exposed as ``Database.disposable``);
* the ``PLEXICHAT_DISPOSABLE_DB=1`` environment marker (read-only: set
  by the operator/CI *before* process start — the server never mutates
  the variable itself);
* the persistent marker row in ``_plexichat_selftest_disposable``;
* the SQLite file path clearly identifies a self-test database;
* ``selftest.require_disposable_db`` is explicitly disabled by the
  operator (config-level opt-out).

Every destructive phase must call :func:`destructive_phase_allowed`
first; refusals are logged and recorded in the shared results so the
suite fails loudly instead of silently wiping live data.
"""

import os
from typing import Any, Dict, Optional, Tuple

import utils.logger as logger

DISPOSABLE_ENV_MARKER = "PLEXICHAT_DISPOSABLE_DB"
_MARKER_TABLE = "_plexichat_selftest_disposable"


def _sqlite_path(db: Any) -> Optional[str]:
    path = getattr(db, "config", {}).get("path") if hasattr(db, "config") else None
    if isinstance(path, str):
        return path
    engine = getattr(db, "engine", None)
    cfg = getattr(engine, "config", None)
    if isinstance(cfg, dict) and isinstance(cfg.get("path"), str):
        return cfg["path"]
    return None


def _marker_row_present(db: Any) -> bool:
    """Best-effort check for the persistent disposable marker row."""
    if db is None:
        return False
    try:
        row = db.fetch_one(f"SELECT 1 FROM {_MARKER_TABLE} LIMIT 1")
        return row is not None
    except Exception:
        return False


def _insert_marker_sql(db: Any) -> str:
    """Engine-aware idempotent INSERT for the disposable marker row.

    SQLite uses ``INSERT OR IGNORE``; PostgreSQL needs the equivalent
    ``ON CONFLICT DO NOTHING`` clause. Both dialects accept ``?``
    placeholders (the Database facade translates them for psycopg).
    """
    columns = "(marker, created_at)"
    values = "VALUES (?, ?)"
    if getattr(db, "type", "") == "sqlite":
        return f"INSERT OR IGNORE INTO {_MARKER_TABLE} {columns} {values}"
    return f"INSERT INTO {_MARKER_TABLE} {columns} {values} ON CONFLICT (marker) DO NOTHING"


def write_disposable_marker(db: Any) -> bool:
    """Persistently flag this database as disposable (idempotent).

    Called at database init when the DB is configured as a test database.
    Never raises: a read-only or not-yet-connected target simply keeps its
    in-memory/env markers.
    """
    if db is None:
        return False
    try:
        db.execute(
            f"CREATE TABLE IF NOT EXISTS {_MARKER_TABLE} ("
            "marker TEXT PRIMARY KEY, created_at INTEGER NOT NULL)"
        )
        import time

        db.execute(
            _insert_marker_sql(db),
            ("disposable", int(time.time())),
        )
        return True
    except Exception as e:
        logger.debug(f"Disposable-DB marker write skipped: {sanitize_error(e)}")
        return False


def is_disposable_database(
    db: Any, selftest_config: Optional[Dict[str, Any]] = None
) -> Tuple[bool, str]:
    """Return ``(disposable, reason)`` for the target database."""
    if selftest_config and selftest_config.get("require_disposable_db") is False:
        return True, "operator disabled require_disposable_db (explicit opt-out)"
    if os.environ.get(DISPOSABLE_ENV_MARKER) == "1":
        return True, f"{DISPOSABLE_ENV_MARKER}=1 environment marker present"
    if db is None:
        return False, "no database connection available to verify disposability"
    if getattr(db, "disposable", False):
        return True, "database.disposable=true (flagged at DB init)"
    path = _sqlite_path(db)
    if path and ("selftest" in os.path.basename(path)):
        return True, f"self-test database path: {path}"
    if _marker_row_present(db):
        return True, f"{_MARKER_TABLE} marker row present"
    return (
        False,
        "target database is not flagged disposable "
        f"(set database.disposable=true, {DISPOSABLE_ENV_MARKER}=1, "
        f"or the {_MARKER_TABLE} marker)",
    )


def destructive_phase_allowed(ctx: Any, action: str) -> bool:
    """Gate a destructive phase; refuse (loudly) on non-disposable targets.

    Refusals are recorded as FAILING results so the suite cannot be
    mistaken for a pass while destructive phases were withheld.
    """
    import src.api as api

    db = api.get_db()
    allowed, reason = is_disposable_database(db, getattr(ctx, "config", {}))
    if allowed:
        logger.debug(f"Destructive phase '{action}' allowed: {reason}")
        return True
    logger.error(f"REFUSED destructive phase '{action}': {reason}")
    results = getattr(ctx, "results", None)
    if isinstance(results, list):
        results.append(
            {
                "method": "DB",
                "path": action,
                "status_code": 0,
                "duration_ms": 0,
                "success": False,
                "label": f"db_guard_{action}",
                "error": sanitize_error(reason),
            }
        )
    return False


def sanitize_error(value: Any, limit: int = 200) -> str:
    """Sanitize raw exception/error strings before storing shared state.

    Error details end up in shared result state that can be surfaced by
    status endpoints, so they are reduced to a single-line, truncated
    summary (exception type + message) with control characters removed.
    """
    if isinstance(value, BaseException):
        text = f"{type(value).__name__}: {value}"
    else:
        try:
            text = str(value)
        except Exception:
            return "<unprintable error>"
    text = " ".join(text.split())
    if len(text) > limit:
        text = text[:limit] + "...[truncated]"
    return text
