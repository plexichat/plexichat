"""
Admin dashboard and system metrics routes.
"""

import asyncio
import json
from contextlib import asynccontextmanager
from typing import Any

from fastapi import APIRouter, Request, HTTPException, Query
from pydantic import BaseModel as _BaseModel
from src.api.schemas.admin import (
    AdminDashboardResponse,
    TelemetryEndpointStat,
    SystemMetrics,
)
from src.api.schemas.common import ErrorResponse
from .utils import (
    check_host_restriction,
    require_admin_permission,
)
from . import _summary_cache
from ._summary_cache import get_or_compute
from src.core.security.hardening.text import strip_control_characters
from src.core import applications
import utils.logger as logger

router = APIRouter()

# Operators poll these read endpoints every few seconds.  A short window
# collapses a burst of polls into one computation without serving data that is
# meaningfully stale.  Scope/host gates run before the cache is consulted.
_SUMMARY_TTL_SECONDS = 2.0


def _safe_label(value: Any) -> Any:
    """Strip control characters from a free-text label before it is emitted.

    Alert sources/severities and telemetry endpoint labels originate from
    internal events but are stored as free text.  A C0/C1 or line-separator
    character in one of those values can forge a log line or split a
    downstream parser, so labels are normalized on the way out.  ``None`` is
    preserved so the response shape does not change.
    """
    if value is None:
        return None
    return strip_control_characters(value)


@router.get(
    "/dashboard",
    response_model=AdminDashboardResponse,
    responses={
        401: {"model": ErrorResponse, "description": "Unauthorized"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_dashboard(request: Request):
    """
    Retrieve overview statistics for the administrator dashboard.

    Returns a summary of system health, active sessions, and recent activity.
    """
    check_host_restriction(request)
    require_admin_permission(request, "metrics.read")
    # The gates above run on every request, including cache hits: authorization
    # is never served from the short-TTL summary cache.
    return get_or_compute(
        "dashboard", (), _build_dashboard_response, _SUMMARY_TTL_SECONDS
    )


def _build_dashboard_response() -> AdminDashboardResponse:
    """Compute the dashboard summary from scratch (cache producer)."""
    from src.core import admin

    try:
        ticket_counts = admin.get_ticket_counts()
        telemetry_stats = []
        try:
            from src.core import telemetry

            if telemetry.is_setup():
                stats = telemetry.get_endpoint_stats(hours=24)
                telemetry_stats = [
                    TelemetryEndpointStat(
                        endpoint=_safe_label(s.endpoint),
                        method=_safe_label(s.method),
                        count=s.count,
                        avg_ms=round(s.avg_response_time_ms, 2),
                        min_ms=round(s.min_response_time_ms, 2)
                        if s.min_response_time_ms is not None
                        else None,
                        max_ms=round(s.max_response_time_ms, 2)
                        if s.max_response_time_ms is not None
                        else None,
                        p50_ms=round(s.p50_response_time_ms, 2)
                        if s.p50_response_time_ms is not None
                        else None,
                        p95_ms=round(s.p95_response_time_ms, 2),
                        p99_ms=round(s.p99_response_time_ms, 2)
                        if s.p99_response_time_ms is not None
                        else None,
                        error_rate=round(s.error_rate, 2),
                        error_count=s.error_count,
                        avg_queries=round(s.avg_queries, 1),
                        avg_query_time_ms=round(s.avg_query_time_ms, 2),
                    )
                    for s in stats[:20]
                ]
        except Exception as te:
            logger.debug(f"Telemetry dashboard stats error: {te}")

        total_users, active_users, scheduled_deletions, db_status = 0, 0, 0, "healthy"
        try:
            counts = applications.get_admin_dashboard_counts()
            total_users = counts["total_users"]
            active_users = counts["active_users"]
            scheduled_deletions = counts["scheduled_deletions"]
            db_status = counts["db_status"]
        except Exception as ue:
            logger.warning(f"User stats dashboard error: {ue}")
            db_status = "degraded"

        # System Metrics
        system_data = None
        try:
            from src.core.admin.system import get_system_metrics

            metrics = get_system_metrics()
            system_data = SystemMetrics(**metrics)
        except Exception as se:
            logger.warning(f"System metrics dashboard error: {se}")

        # New feature stats
        feature_stats = {}
        try:
            feature_stats = applications.get_admin_dashboard_feature_stats()
            # Report categories are rendered as chart labels; never emit raw
            # control characters from a stored category value.
            categories = feature_stats.get("report_categories")
            if isinstance(categories, list):
                feature_stats["report_categories"] = [
                    {**item, "category": _safe_label(item.get("category"))}
                    for item in categories
                    if isinstance(item, dict)
                ]
        except Exception as fe:
            logger.warning(f"Feature stats dashboard error: {fe}")

        import utils.version as version_util

        current_version = version_util.current_string()

        # Cross-worker event listener health
        worker_health = None
        try:
            from src.api.websocket import cross_worker_listener_status

            worker_health = cross_worker_listener_status()
        except Exception as we:
            logger.debug(f"Worker health check error: {we}")

        return AdminDashboardResponse(
            tickets=ticket_counts,
            telemetry=telemetry_stats,
            total_users=total_users,
            active_users=active_users,
            scheduled_deletions=scheduled_deletions,
            db_status=db_status,
            system=system_data,
            server_version=current_version,
            feature_stats=feature_stats,
            worker_health=worker_health,
        )
    except Exception as e:
        logger.error(f"Dashboard data error: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


def warm_admin_dashboard_summary_cache() -> None:
    """Prime the dashboard summary off the request path (best-effort).

    ``GET /admin/dashboard`` is polled frequently by the admin UI, and its
    first call after a restart assembles the whole response (ticket counts,
    telemetry endpoint stats, dashboard counts, system metrics, feature stats
    and worker health) while holding a request open. That assembly is
    request-invariant: the host/permission gates run in the route *before* the
    cache is consulted, and the payload is a system-wide summary with no
    per-admin data. Priming the same ``("dashboard", ())`` entry here moves the
    one-off cost onto the boot worker thread so the first real poll is a cache
    hit. Failures are logged and swallowed -- the route still builds on demand.
    """
    _summary_cache.warm(
        "dashboard", (), _build_dashboard_response, _SUMMARY_TTL_SECONDS
    )


@asynccontextmanager
async def _dashboard_summary_lifespan(app: Any):
    """Run the cold summary build at boot, off the event loop and requests."""
    try:
        await asyncio.get_running_loop().run_in_executor(
            None, warm_admin_dashboard_summary_cache
        )
    except Exception as e:
        logger.debug(f"Admin dashboard summary warm-up failed: {e}")
    yield


# FastAPI merges this into the application lifespan via include_router().
router.lifespan_context = _dashboard_summary_lifespan


@router.get(
    "/dashboard/cross-worker-tuning",
    responses={
        401: {"model": ErrorResponse, "description": "Unauthorized"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_cross_worker_tuning(request: Request):
    """Return the current poll-loop tuning constants."""
    check_host_restriction(request)
    require_admin_permission(request, "admin.dashboard.tuning")

    try:
        from src.api.websocket import get_poll_tuning

        return get_poll_tuning()
    except Exception as e:
        logger.debug(f"Cross-worker tuning read error: {e}")
        raise HTTPException(status_code=500, detail="Failed to read tuning")


class _TuningPatch(_BaseModel):
    """Optional overrides for poll-loop tuning."""

    poll_interval_sec: float | None = None
    heartbeat_interval_polls: int | None = None
    max_backoff_sec: float | None = None
    backoff_reset_after_polls: int | None = None
    initial_backoff_sec: float | None = None
    glide_request_timeout_ms: int | None = None


# Sanity bounds for runtime tuning constants. Values outside these ranges are
# rejected outright (<=0 would spin the poll loop; absurd upper bounds would
# degrade or DoS the realtime layer).
_TUNING_BOUNDS = {
    "poll_interval_sec": (0.001, 5.0),
    "heartbeat_interval_polls": (1, 10_000),
    "max_backoff_sec": (0.01, 300.0),
    "backoff_reset_after_polls": (1, 100_000),
    "initial_backoff_sec": (0.01, 60.0),
    "glide_request_timeout_ms": (10, 600_000),
}


def _validate_tuning_bounds(updates: dict) -> None:
    for key, value in updates.items():
        bounds = _TUNING_BOUNDS.get(key)
        if not bounds:
            continue
        low, high = bounds
        if value <= 0 or value < low or value > high:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": {
                        "code": 400,
                        "message": f"Tuning value '{key}'={value} out of allowed range [{low}, {high}]",
                    }
                },
            )


@router.patch(
    "/dashboard/cross-worker-tuning",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Unauthorized"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def patch_cross_worker_tuning(request: Request, body: _TuningPatch):
    """Hot-reload poll-loop tuning constants at runtime.

    Requires the ``admin.dashboard.tuning`` permission. Only the keys
    provided (non-null) are updated; omitted keys keep their current value.
    Supplied constants are bounds-validated before being applied. Returns
    the full tuning dict after the change. Every hot-reload event is
    recorded in the admin audit log (old → new values, admin ID, IP).
    """
    check_host_restriction(request)
    admin_id = require_admin_permission(request, "admin.dashboard.tuning")

    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(status_code=400, detail="No tuning keys provided")
    _validate_tuning_bounds(updates)

    # Snapshot old values for the audit trail *before* applying changes.
    old_tuning: dict = {}
    try:
        from src.api.websocket import get_poll_tuning

        old_tuning = get_poll_tuning()
    except Exception as audit_error:
        # Audit is best-effort; the tuning update below still proceeds.
        logger.debug(f"dashboard tuning audit failed: {audit_error}")

    try:
        from src.api.websocket import update_poll_tuning

        result = update_poll_tuning(updates)
    except Exception as e:
        logger.warning(f"Cross-worker tuning update error: {e}")
        raise HTTPException(status_code=500, detail="Failed to update tuning")

    # Audit log — best-effort, must not affect the response.
    try:
        import src.api as _api

        db = _api.get_db()
        if db is not None:
            ip = request.client.host if request.client else "unknown"
            from src.core.admin.logging import AdminLogEntry, get_admin_logger

            # Resolve admin username for the audit trail.
            username = None
            row = db.fetch_one(
                "SELECT username FROM admin_users WHERE id = ?", (admin_id,)
            )
            if row:
                username = row.get("username") if isinstance(row, dict) else row[0]

            changes = {
                k: {"old": old_tuning.get(k), "new": result.get(k)} for k in updates
            }
            entry = AdminLogEntry(
                admin_id=admin_id,
                action="cross_worker_tuning_update",
                target_type="system",
                details=json.dumps(changes),
                ip_address=ip,
                user_agent=request.headers.get("user-agent"),
                metadata={"admin_username": username} if username else None,
            )
            get_admin_logger().log_action(db, entry)
    except Exception:
        logger.debug("Cross-worker tuning audit log write failed (non-fatal)")

    return result


@router.get(
    "/dashboard/cross-worker-tuning/audit",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid query parameters"},
        401: {"model": ErrorResponse, "description": "Unauthorized"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_cross_worker_tuning_audit(
    request: Request,
    limit: int = Query(20, ge=1, le=200),
):
    """Return recent cross-worker tuning audit log entries."""
    check_host_restriction(request)
    # This reads the same admin_audit_log (admin ids, usernames, IPs, details)
    # as the /audit endpoints, so it must honour the same audit-read scope.
    require_admin_permission(request, "admin.audit.read")

    try:
        import src.api as _api

        db = _api.get_db()
        if db is None:
            raise HTTPException(status_code=500, detail="Database not available")

        rows = db.fetch_all(
            """
            SELECT a.id, a.admin_id, u.username, a.details,
                   a.ip_address, a.status, a.created_at
            FROM admin_audit_log a
            LEFT JOIN admin_users u ON a.admin_id = u.id
            WHERE a.action = 'cross_worker_tuning_update'
            ORDER BY a.created_at DESC
            LIMIT ?
            """,
            (limit,),
        )

        entries = []
        for row in rows:
            if isinstance(row, dict):
                entries.append(
                    {
                        # ``id`` is the audit row's rowid; ``admin_id`` is the
                        # acting admin's snowflake and is emitted as a string.
                        "id": row["id"],
                        "admin_id": str(row["admin_id"]),
                        "username": row.get("username") or f"admin#{row['admin_id']}",
                        "details": row.get("details"),
                        "ip_address": row.get("ip_address"),
                        "status": row.get("status"),
                        "created_at": row["created_at"],
                    }
                )
            else:
                entries.append(
                    {
                        "id": row[0],
                        "admin_id": str(row[1]),
                        "username": row[2] or f"admin#{row[1]}",
                        "details": row[3],
                        "ip_address": row[4],
                        "status": row[5],
                        "created_at": row[6],
                    }
                )

        return {"entries": entries}
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"Cross-worker tuning audit query error: {e}")
        raise HTTPException(status_code=500, detail="Failed to query audit log")


@router.get(
    "/dashboard/system-alerts",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid query parameters"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {"model": ErrorResponse, "description": "Insufficient permissions"},
    },
)
async def get_system_alerts(
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    hours: int = Query(24, ge=1, le=8760),
    source: str | None = Query(None, max_length=64),
    severity: str | None = Query(None, max_length=32),
):
    """Return recent system alerts across all subsystems."""
    check_host_restriction(request)
    require_admin_permission(request, "metrics.read")

    def _load() -> dict:
        try:
            import src.api as _api
            import time

            db = _api.get_db()
            if db is None:
                raise HTTPException(status_code=500, detail="Database not available")

            cutoff = int(time.time()) - (hours * 3600)
            where = ["created_at >= ?"]
            params: list = [cutoff]
            if source:
                where.append("source = ?")
                params.append(source)
            if severity:
                where.append("severity = ?")
                params.append(severity)
            params.append(limit)

            rows = db.fetch_all(
                f"SELECT id, source, event_type, severity, details, target_path, created_at "
                f"FROM system_alerts "
                f"WHERE {' AND '.join(where)} "
                f"ORDER BY created_at DESC LIMIT ?",
                tuple(params),
            )

            entries: list[dict] = []
            for row in rows:
                if isinstance(row, dict):
                    entries.append(
                        {
                            "id": row["id"],
                            "source": _safe_label(row["source"]),
                            "event_type": _safe_label(row["event_type"]),
                            "severity": _safe_label(row["severity"]),
                            "details": row.get("details"),
                            "target_path": row.get("target_path"),
                            "created_at": row["created_at"],
                        }
                    )
                else:
                    entries.append(
                        {
                            "id": row[0],
                            "source": _safe_label(row[1]),
                            "event_type": _safe_label(row[2]),
                            "severity": _safe_label(row[3]),
                            "details": row[4],
                            "target_path": row[5],
                            "created_at": row[6],
                        }
                    )

            return {"entries": entries}
        except HTTPException:
            raise
        except Exception as e:
            logger.warning(f"System alerts query error: {e}")
            raise HTTPException(status_code=500, detail="Failed to query system alerts")

    return get_or_compute(
        "system_alerts",
        (limit, hours, source, severity),
        _load,
        _SUMMARY_TTL_SECONDS,
    )


@router.get(
    "/dashboard/system-alerts/stats",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid query parameters"},
        401: {"model": ErrorResponse, "description": "Unauthorized"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_system_alert_stats(
    request: Request,
    hours: int = Query(168, ge=1, le=8760),
):
    """Return alert counts grouped by source and severity."""
    check_host_restriction(request)
    require_admin_permission(request, "metrics.read")

    def _load() -> dict:
        try:
            import src.api as _api
            import time

            db = _api.get_db()
            if db is None:
                raise HTTPException(status_code=500, detail="Database not available")

            cutoff = int(time.time()) - (hours * 3600)
            # Bound the number of label buckets.  Without a LIMIT a subsystem
            # emitting a distinct source/severity pair could make this
            # aggregate grow without limit and turn a cheap stats read into a
            # large response.  The 200 most frequent pairs are plenty for the
            # dashboard's chart.
            rows = db.fetch_all(
                "SELECT source, severity, COUNT(*) as cnt "
                "FROM system_alerts "
                "WHERE created_at >= ? "
                "GROUP BY source, severity "
                "ORDER BY source, severity "
                "LIMIT 200",
                (cutoff,),
            )

            stats: dict[str, dict[str, int]] = {}
            total = 0
            for row in rows:
                if isinstance(row, dict):
                    src, sev, cnt = row["source"], row["severity"], row["cnt"]
                else:
                    src, sev, cnt = row[0], row[1], row[2]
                # Labels come from free-text event fields; strip control chars
                # before they become dict keys (chart labels / log lines).
                stats.setdefault(_safe_label(src), {})[_safe_label(sev)] = cnt
                total += cnt

            return {"hours": hours, "total": total, "by_source": stats}
        except HTTPException:
            raise
        except Exception as e:
            logger.warning(f"System alert stats error: {e}")
            raise HTTPException(status_code=500, detail="Failed to query alert stats")

    return get_or_compute("system_alert_stats", (hours,), _load, _SUMMARY_TTL_SECONDS)
