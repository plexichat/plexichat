"""
Admin telemetry routes.
"""

from fastapi import APIRouter, Request, HTTPException, Response, Query
from typing import Optional
import time
import urllib.parse
import csv
import io
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
        if not telemetry.is_setup():
            return TelemetryStatsResponse(stats=[], source=source or "all")
        cid = "server" if source == "server" else None
        stats = telemetry.get_endpoint_stats(hours=hours, endpoint_filter=endpoint, client_id_filter=cid)
        return TelemetryStatsResponse(
            stats=[TelemetryEndpointStat(
                endpoint=urllib.parse.unquote(s.endpoint), method=s.method, count=s.count,
                avg_ms=round(s.avg_response_time_ms, 2), p95_ms=round(s.p95_response_time_ms, 2),
                error_rate=round(s.error_rate, 2), error_count=s.error_count,
                avg_queries=round(s.avg_queries, 1),
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
        if not telemetry.is_setup():
            return TelemetryHistoryResponse(history=[])
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
        if not telemetry.is_setup():
            return TelemetryResetResponse(success=False, deleted_count=0)
        telemetry.reset_all_stats()
        return TelemetryResetResponse(success=True, deleted_count=0)
    except Exception as e:
        logger.error(f"Telemetry reset error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Internal server error"}})

@router.get("/telemetry/export")
async def export_telemetry_stats(
    request: Request, 
    format: str = "json", 
    hours: int = 24,
    token: Optional[str] = Query(None, alias="Authorization")
):
    # Allow token in query param for downloads
    if token:
        if token.startswith("Bearer "):
            token = token[7:]
        # Manual validation if using query param
        from src.core import admin
        if not admin.validate_session(token):
            raise HTTPException(status_code=401, detail="Unauthorized")
    else:
        check_host_restriction(request)
        get_admin_from_token(request)

    try:
        from src.core import telemetry
        if not telemetry.is_setup():
            raise HTTPException(status_code=500, detail="Telemetry not setup")
        stats = telemetry.get_endpoint_stats(hours=hours)
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        
        if format == "json":
            import json
            data = TelemetryExportResponse(
                export_time=ts, hours=hours, 
                stats=[TelemetryEndpointStat(
                    endpoint=s.endpoint, method=s.method, count=s.count, 
                    avg_ms=round(s.avg_response_time_ms, 2), p95_ms=round(s.p95_response_time_ms, 2), 
                    error_rate=round(s.error_rate, 2), error_count=s.error_count,
                    avg_queries=round(s.avg_queries, 1), 
                    avg_query_time_ms=round(s.avg_query_time_ms, 2)
                ) for s in stats]
            ).model_dump()
            
            return Response(
                content=json.dumps(data, indent=2),
                media_type="application/json",
                headers={"Content-Disposition": f"attachment; filename=telemetry_{ts.replace(' ', '_').replace(':', '-')}.json"}
            )
        
        elif format == "csv":
            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow(["Endpoint", "Method", "Hits", "Avg Latency (ms)", "P95 Latency (ms)", "Avg Queries", "Avg DB Time (ms)", "Error Rate %"])
            for s in stats:
                writer.writerow([s.endpoint, s.method, s.count, round(s.avg_response_time_ms, 2), round(s.p95_response_time_ms, 2), round(s.avg_queries, 1), round(s.avg_query_time_ms, 2), round(s.error_rate, 2)])
            return Response(
                content=output.getvalue(),
                media_type="text/csv",
                headers={"Content-Disposition": f"attachment; filename=telemetry_{ts.replace(' ', '_').replace(':', '-')}.csv"}
            )
            
        elif format == "txt":
            lines = [f"PlexiChat Telemetry Export - {ts}", f"Time Window: {hours} hours", ""]
            header = f"{'Endpoint':<50} {'Method':<8} {'Hits':>8} {'Avg':>8} {'P95':>8} {'Q/Req':>6} {'DB ms':>8} {'Err%':>6}"
            lines.append(header)
            lines.append("-" * len(header))
            for s in stats:
                lines.append(f"{s.endpoint[:50]:<50} {s.method:<8} {s.count:>8} {s.avg_response_time_ms:>8.1f} {s.p95_response_time_ms:>8.1f} {s.avg_queries:>6.1f} {s.avg_query_time_ms:>8.1f} {s.error_rate:>6.1f}")
            return Response(
                content="\n".join(lines),
                media_type="text/plain",
                headers={"Content-Disposition": f"attachment; filename=telemetry_{ts.replace(' ', '_').replace(':', '-')}.txt"}
            )

        raise HTTPException(status_code=400, detail=f"Unsupported format: {format}")
    except Exception as e:
        if isinstance(e, HTTPException):
            raise
        logger.error(f"Telemetry export error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")
