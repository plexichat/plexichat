"""Shared system-alert recording helper.

Importable by both ``src.api`` and ``src.core`` modules without circular
imports because the DB access is lazy (imported inside the function body).
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def record_system_alert(
    event_type: str = "",
    details: dict[str, Any] | None = None,
    *,
    source: str = "cross_worker",
    severity: str = "info",
    target_path: str | None = None,
) -> None:
    """Persist a system alert to the database.

    Best-effort — failures never propagate to callers, but they are
    logged at WARNING (instead of silently dropped) so a broken alert
    pipeline is diagnosable.

    Args:
        source: Subsystem identifier (cross_worker, valkey, db_pool, approvals, …).
        event_type: Machine-readable event name (connected, dead, …).
        details: Optional JSON-serialisable dict with extra context.
        severity: info, warning, error, or critical.
        target_path: URL hash or path for the jump-to link in the Alerts tab.
    """
    try:
        import time
        import json as _json
        import src.api as _api
        from src.utils.encryption import generate_snowflake_id

        db = _api.get_db()
        if db is None:
            return
        # ``id`` is supplied explicitly: the schema is authored in SQLite,
        # where ``INTEGER PRIMARY KEY`` auto-assigns a rowid, but the Postgres
        # form is a plain ``BIGINT PRIMARY KEY`` with no default. Omitting it
        # there failed every insert with
        # ``null value in column "id" of relation "system_alerts"``, so the
        # alert pipeline recorded nothing at all on a Postgres install.
        db.execute(
            "INSERT INTO system_alerts "
            "(id, source, event_type, severity, details, target_path, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                generate_snowflake_id(),
                source,
                event_type,
                severity,
                _json.dumps(details) if details is not None else None,
                target_path,
                int(time.time()),
            ),
        )
    except Exception as exc:
        # Stay non-raising so callers are never disrupted by DB issues,
        # but surface the loss at WARNING: a failing alert pipeline would
        # otherwise drop alerts with zero diagnosability.
        logger.warning(
            "system_alerts: failed to record %r alert (source=%r, severity=%r): %s",
            event_type,
            source,
            severity,
            exc,
        )
