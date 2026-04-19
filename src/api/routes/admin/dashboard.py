"""
Admin dashboard and system metrics routes.
"""

from fastapi import APIRouter, Request, HTTPException
import time
from src.api.schemas.admin import (
    AdminDashboardResponse,
    TelemetryEndpointStat,
    SystemMetrics,
)
from .utils import check_host_restriction, get_admin_from_token
import src.api as api
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
            db = api.get_db()
            if db:
                total_users = db.fetch_one("SELECT COUNT(*) as c FROM auth_users")["c"]
                cutoff = int((time.time() - 86400) * 1000)
                active_users = db.fetch_one(
                    "SELECT COUNT(*) as c FROM auth_users WHERE last_login_at > ?",
                    (cutoff,),
                )["c"]
                scheduled_deletions = db.fetch_one(
                    "SELECT COUNT(*) as c FROM auth_users WHERE deletion_status = 'frozen'"
                )["c"]
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
            db = api.get_db()
            if db:
                # Bookmarks count
                try:
                    row = db.fetch_one("SELECT COUNT(*) as c FROM user_bookmarks")
                    feature_stats["bookmarks"] = row["c"] if row else 0
                except Exception:
                    feature_stats["bookmarks"] = 0

                # Scheduled messages count
                try:
                    row = db.fetch_one(
                        "SELECT COUNT(*) as c FROM scheduled_messages WHERE status = 'pending'"
                    )
                    feature_stats["scheduled_messages_pending"] = row["c"] if row else 0
                except Exception:
                    feature_stats["scheduled_messages_pending"] = 0

                # Forwarded messages count
                try:
                    row = db.fetch_one("SELECT COUNT(*) as c FROM forwarded_messages")
                    feature_stats["forwarded_messages"] = row["c"] if row else 0
                except Exception:
                    feature_stats["forwarded_messages"] = 0

                # Voice messages count
                try:
                    row = db.fetch_one(
                        "SELECT COUNT(*) as c FROM msg_messages WHERE is_voice = 1"
                    )
                    feature_stats["voice_messages"] = row["c"] if row else 0
                except Exception:
                    feature_stats["voice_messages"] = 0

                # User profiles with custom status
                try:
                    row = db.fetch_one(
                        "SELECT COUNT(*) as c FROM user_profiles WHERE status IS NOT NULL"
                    )
                    feature_stats["profiles_with_status"] = row["c"] if row else 0
                except Exception:
                    feature_stats["profiles_with_status"] = 0

                # Push tokens registered
                try:
                    row = db.fetch_one(
                        "SELECT COUNT(*) as c FROM push_tokens WHERE active = 1"
                    )
                    feature_stats["push_tokens_active"] = row["c"] if row else 0
                except Exception:
                    feature_stats["push_tokens_active"] = 0

                # Webhook retry queue
                try:
                    row = db.fetch_one(
                        "SELECT COUNT(*) as c FROM webhook_retry_queue WHERE status = 'pending'"
                    )
                    feature_stats["webhook_retries_pending"] = row["c"] if row else 0
                except Exception:
                    feature_stats["webhook_retries_pending"] = 0

                # Reports by category
                try:
                    rows = db.fetch_all(
                        "SELECT category, COUNT(*) as c FROM message_reports GROUP BY category ORDER BY c DESC LIMIT 10"
                    )
                    feature_stats["report_categories"] = [
                        {"category": r["category"], "count": r["c"]} for r in rows
                    ]
                except Exception:
                    feature_stats["report_categories"] = []

                # Active DM spam filters
                try:
                    row = db.fetch_one(
                        "SELECT COUNT(*) as c FROM dm_spam_filters WHERE enabled = 1"
                    )
                    feature_stats["dm_spam_filters_active"] = row["c"] if row else 0
                except Exception:
                    feature_stats["dm_spam_filters_active"] = 0

                # Threads with slowmode
                try:
                    row = db.fetch_one(
                        "SELECT COUNT(*) as c FROM thread_threads WHERE slowmode_interval_ms > 0"
                    )
                    feature_stats["threads_with_slowmode"] = row["c"] if row else 0
                except Exception:
                    feature_stats["threads_with_slowmode"] = 0
        except Exception as fe:
            logger.warning(f"Feature stats dashboard error: {fe}")

        import utils.version as version_util

        current_version = version_util.current_string()

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
        )
    except Exception as e:
        logger.error(f"Dashboard data error: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )
