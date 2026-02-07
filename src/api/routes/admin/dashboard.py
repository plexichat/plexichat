"""
Admin dashboard and system metrics routes.
"""

from fastapi import APIRouter, Request, HTTPException
import time
from typing import List
from src.api.schemas.admin import AdminDashboardResponse, TelemetryEndpointStat, SystemMetrics
from .utils import check_host_restriction, get_admin_from_token
import src.api as api
import utils.logger as logger

router = APIRouter()

@router.get("/dashboard", response_model=AdminDashboardResponse)
async def get_dashboard(request: Request):
    check_host_restriction(request)
    admin_id = get_admin_from_token(request)
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
                        endpoint=s.endpoint, method=s.method, count=s.count,
                        avg_ms=round(s.avg_response_time_ms, 2),
                        p95_ms=round(s.p95_response_time_ms, 2),
                        error_rate=round(s.error_rate, 2),
                        error_count=s.error_count,
                        avg_queries=round(s.avg_queries, 1),
                        avg_query_time_ms=round(s.avg_query_time_ms, 2)
                    )
                    for s in stats[:20]
                ]
        except Exception as te:
            logger.debug(f"Telemetry dashboard stats error: {te}")

        total_users, active_users, db_status = 0, 0, "healthy"
        try:
            db = api.get_db()
            total_users = db.fetch_one("SELECT COUNT(*) as c FROM auth_users")["c"]
            cutoff = int((time.time() - 86400) * 1000)
            active_users = db.fetch_one("SELECT COUNT(*) as c FROM auth_users WHERE last_login_at > ?", (cutoff,))["c"]
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

        return AdminDashboardResponse(
            tickets=ticket_counts, 
            telemetry=telemetry_stats,
            total_users=total_users,
            active_users=active_users,
            db_status=db_status,
            system=system_data
        )
    except Exception as e:
        logger.error(f"Dashboard data error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": str(e)}})
