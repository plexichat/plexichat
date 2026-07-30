"""
Admin dashboard and system metrics routes.
"""

from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel as _BaseModel
from src.api.schemas.admin import (
    AdminDashboardResponse,
    TelemetryEndpointStat,
    SystemMetrics,
)
from .utils import check_host_restriction, get_admin_from_token
from src.core import applications
import utils.logger as logger

router = APIRouter()


@router.get("/dashboard", response_model=AdminDashboardResponse)
async def get_dashboard(request: Request):
    """
    Retrieve overview statistics for the administrator dashboard.

    Returns a summary of system health, active sessions, and recent activity.
    """
    check_host_restriction(request)
    get_admin_from_token(request)
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
                        endpoint=s.endpoint,
                        method=s.method,
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
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )


@router.get("/dashboard/cross-worker-tuning")
async def get_cross_worker_tuning(request: Request):
    """Return the current poll-loop tuning constants."""
    check_host_restriction(request)
    get_admin_from_token(request)

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


@router.patch("/dashboard/cross-worker-tuning")
async def patch_cross_worker_tuning(request: Request, body: _TuningPatch):
    """Hot-reload poll-loop tuning constants at runtime.

    Only the keys provided (non-null) are updated; omitted keys keep
    their current value.  Returns the full tuning dict after the
    change, with ``applied`` and ``rejected`` lists so the caller
    knows exactly what took effect.
    """
    check_host_restriction(request)
    get_admin_from_token(request)

    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(status_code=400, detail="No tuning keys provided")

    try:
        from src.api.websocket import update_poll_tuning

        result = update_poll_tuning(updates)
        return result
    except Exception as e:
        logger.warning(f"Cross-worker tuning update error: {e}")
        raise HTTPException(status_code=500, detail="Failed to update tuning")
