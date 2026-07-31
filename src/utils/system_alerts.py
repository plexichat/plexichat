"""Shared system-alert recording helper.

Importable by both ``src.api`` and ``src.core`` modules without circular
imports because the DB access is lazy (imported inside the function body).
"""

from __future__ import annotations

from typing import Any


def record_system_alert(
    event_type: str = "",
    details: dict[str, Any] | None = None,
    *,
    source: str = "cross_worker",
    severity: str = "info",
    target_path: str | None = None,
) -> None:
    """Persist a system alert to the database.

    Best-effort — failures are silently swallowed so callers are
    never disrupted by DB issues.

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

        db = _api.get_db()
        if db is None:
            return
        db.execute(
            "INSERT INTO system_alerts "
            "(source, event_type, severity, details, target_path, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                source,
                event_type,
                severity,
                _json.dumps(details) if details else None,
                target_path,
                int(time.time()),
            ),
        )
    except Exception:
        pass
