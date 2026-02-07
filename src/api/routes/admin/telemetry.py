"""
Admin telemetry routes.
"""

from fastapi import APIRouter, Request, HTTPException, Response, status
from typing import List, Optional, Union
import time
import urllib.parse
from src.api.schemas.admin import (
    TelemetryStatsResponse, TelemetryEndpointStat, TelemetryHistoryResponse, TelemetryHistoryBucket,
    TelemetryResetResponse, TelemetryExportResponse
)
from .utils import check_host_restriction, get_admin_from_token
import utils.logger as logger

router = APIRouter()

@router.get("/telemetry/stats", response_model=TelemetryStatsResponse)
async def get_telemetry_stats(request: Request, hours: int = 24, endpoint: Optional[str] = None, source: Optional[str] = None):
    check_host_restriction(request)
    get_admin_from_token(request)
    try:
        from src.core import telemetry
        if not telemetry.is_setup(): return TelemetryStatsResponse(stats=[], source=source or "all")
        cid = "server" if source == "server" else None
        stats = telemetry.get_endpoint_stats(hours=hours, endpoint_filter=endpoint, client_id_filter=cid)
        return TelemetryStatsResponse(
            stats=[TelemetryEndpointStat(
                endpoint=urllib.parse.unquote(s.endpoint), method=s.method, count=s.count,
                avg_ms=round(s.avg_response_time_ms, 2), p95_ms=round(s.p95_response_time_ms, 2),
                error_rate=round(s.error_rate * 100, 2), avg_queries=round(s.avg_queries, 1),
                avg_query_time_ms=round(s.avg_query_time_ms, 2)
            ) for s in stats],
            source=source or "all"
        )
    except Exception as e:
        logger.error(f"Telemetry stats error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Internal server error"}})

@router.get("/telemetry/history", response_model=TelemetryHistoryResponse)
async def get_telemetry_history(request: Request, endpoint: str, method: str = "GET", hours: int = 24, bucket_minutes: int = 5):
    check_host_restriction(request)
    get_admin_from_token(request)
    try:
        from src.core import telemetry
        if not telemetry.is_setup(): return TelemetryHistoryResponse(history=[])
        history = telemetry.get_response_time_history(endpoint=endpoint, method=method.upper(), hours=hours, bucket_minutes=bucket_minutes)
        return TelemetryHistoryResponse(history=[TelemetryHistoryBucket(**h) for h in history])
    except Exception as e:
        logger.error(f"Telemetry history error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Internal server error"}})

@router.post("/telemetry/reset", response_model=TelemetryResetResponse)
async def reset_telemetry_stats(request: Request):
    check_host_restriction(request)
    get_admin_from_token(request)
    try:
        from src.core import telemetry
        if not telemetry.is_setup(): return TelemetryResetResponse(success=False, deleted_count=0)
        return TelemetryResetResponse(success=True, deleted_count=telemetry.reset_all_stats())
    except Exception as e:
        logger.error(f"Telemetry reset error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Internal server error"}})

@router.get("/telemetry/export")
async def export_telemetry_stats(request: Request, format: str = "json", hours: int = 24):
    check_host_restriction(request)
    get_admin_from_token(request)
    try:
        from src.core import telemetry
        if not telemetry.is_setup(): raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Telemetry not setup"}})
        stats = telemetry.get_endpoint_stats(hours=hours)
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        if format == "json":
            return TelemetryExportResponse(export_time=ts, hours=hours, stats=[TelemetryEndpointStat(endpoint=s.endpoint, method=s.method, count=s.count, avg_ms=round(s.avg_response_time_ms, 2), p95_ms=round(s.p95_response_time_ms, 2), error_rate=round(s.error_rate * 100, 2), avg_queries=round(s.avg_queries, 1), avg_query_time_ms=round(s.avg_query_time_ms, 2)) for s in stats])
        # Add other formats if needed
        raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": f"Unsupported format: {format}"}})
    except Exception as e:
        if isinstance(e, HTTPException): raise
        logger.error(f"Telemetry export error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Internal server error"}})
