"""
Telemetry API routes.

Handles client-submitted response time telemetry data.
"""

from fastapi import APIRouter, Depends, HTTPException, status, Request
from typing import Optional
import json

import utils.logger as logger
from src.api.dependencies import get_optional_user
from src.core.auth.models import TokenInfo
from src.api.schemas.telemetry import TelemetrySubmission, TelemetryResponse
from src.api.schemas.common import ErrorResponse
from src.core import ratelimit


router = APIRouter(prefix="/telemetry", tags=["Telemetry"])


@router.post(
    "/response-times",
    response_model=TelemetryResponse,
    summary="Submit response times",
    responses={
        429: {"model": ErrorResponse, "description": "Too many requests"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def submit_response_times(
    submission: TelemetrySubmission,
    request: Request,
    current_user: Optional[TokenInfo] = Depends(get_optional_user),
) -> TelemetryResponse:
    """
    Submit client-side response time data.

    Data is stored for analysis of client-perceived performance.
    """
    client_ip = request.client.host if request.client else "unknown"

    rl_result = ratelimit.check_rate_limit(
        ip_address=client_ip, route="POST /telemetry"
    )
    if not rl_result.allowed:
        logger.warning(f"Telemetry submission rate limit exceeded for {client_ip}")
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "error": {
                    "code": 429,
                    "message": "Telemetry submission rate limit exceeded",
                }
            },
        )

    user_id = current_user.user_id if current_user else None

    try:
        from src.api import get_telemetry

        telemetry = get_telemetry()

        if not telemetry:
            # Silently ignore if telemetry is disabled, but still count against rate limit
            logger.info(
                f"Telemetry submission received from {client_ip} (user: {user_id}) but telemetry module is not available"
            )
            return TelemetryResponse(
                accepted=0, message="Telemetry module not available"
            )

        accepted_count = 0
        try:
            # Map API schema to core module expected format
            core_entries = [
                {
                    "endpoint": entry.endpoint,
                    "method": entry.method,
                    "response_time_ms": entry.response_time_ms,
                    "status_code": entry.status_code,
                    "timestamp": entry.timestamp,
                    "db_queries": entry.db_queries,
                    "db_time_ms": entry.db_time_ms,
                }
                for entry in submission.entries
            ]
            
            accepted_count = telemetry.submit_response_times(
                entries=core_entries,
                client_id=str(user_id) if user_id else None
            )
        except Exception as te:
            logger.error(
                f"Error recording telemetry entries from {client_ip} (user: {user_id}): {te}",
                exc_info=True,
            )
            # Continue to return what we have

        if accepted_count > 0:
            logger.debug(
                f"Accepted {accepted_count} telemetry entries from {client_ip} (user: {user_id})"
            )

        return TelemetryResponse(
            accepted=accepted_count,
            message=f"Successfully accepted {accepted_count} telemetry entries",
        )
    except Exception as e:
        logger.error(
            f"Failed to process telemetry submission from {client_ip} (user: {user_id}): {e}",
            exc_info=True,
        )
        # Return success to client even if recording fails (don't break client app)
        return TelemetryResponse(accepted=0, message="Telemetry processing failed")


@router.post(
    "/csp-report",
    summary="Submit CSP violation report",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def submit_csp_report(
    request: Request,
):
    """
    Submit a Content-Security-Policy violation report.
    
    The browser sends this data when a CSP violation occurs.
    """
    try:
        # CSP reports use application/csp-report or application/json
        body = await request.body()
        try:
            report = json.loads(body)
        except Exception:
            report = {"raw": body.decode(errors='ignore')}
            
        client_ip = request.client.host if request.client else "unknown"
        logger.warning(f"CSP Violation reported from {client_ip}: {json.dumps(report)}")
        
        # Optionally record this in telemetry
        from src.api import get_telemetry
        telemetry = get_telemetry()
        if telemetry and hasattr(telemetry, 'record_csp_violation'):
            telemetry.record_csp_violation(report, client_ip)
            
    except Exception as e:
        logger.error(f"Error processing CSP report: {e}")
        
    return None
