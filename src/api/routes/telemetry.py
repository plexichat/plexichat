"""
Telemetry API routes.

Handles client-submitted response time telemetry data.
"""

from fastapi import APIRouter, Depends, HTTPException, status, Request
from typing import List, Optional, Any
import time

import utils.config as config
import utils.logger as logger
from src.api.dependencies import get_optional_user
from src.core.auth.models import TokenInfo
from src.api.schemas.telemetry import TelemetrySubmission, TelemetryResponse
from src.api.schemas.common import ErrorResponse


router = APIRouter(prefix="/telemetry", tags=["Telemetry"])


# Rate limiting for telemetry submissions (per client IP)
_telemetry_rate_limits = {}


def _get_rate_limit_config():
    """Get rate limit configuration with defaults."""
    telemetry_config = config.get("telemetry", {})
    rate_limit_config = telemetry_config.get("rate_limit", {})
    return {
        "max_per_minute": rate_limit_config.get("max_per_minute", 2),  # Reduced from 10
        "max_per_hour": rate_limit_config.get("max_per_hour", 10),  # New hourly limit
        "max_entries_per_submission": rate_limit_config.get(
            "max_entries_per_submission", 100
        ),
    }


def _check_rate_limit(client_ip: str) -> bool:
    """Check if client is rate limited for telemetry submissions."""
    now = time.time()
    rate_config = _get_rate_limit_config()

    if client_ip not in _telemetry_rate_limits:
        _telemetry_rate_limits[client_ip] = []

    # Clean old entries (keep last hour)
    hour_ago = now - 3600
    _telemetry_rate_limits[client_ip] = [
        t for t in _telemetry_rate_limits[client_ip] if t > hour_ago
    ]

    # Check hourly limit
    if len(_telemetry_rate_limits[client_ip]) >= rate_config["max_per_hour"]:
        return False

    # Check per-minute limit
    minute_ago = now - 60
    recent_count = sum(1 for t in _telemetry_rate_limits[client_ip] if t > minute_ago)
    if recent_count >= rate_config["max_per_minute"]:
        return False

    return True


def _record_submission(client_ip: str):
    """Record telemetry submission for rate limiting."""
    now = time.time()
    if client_ip not in _telemetry_rate_limits:
        _telemetry_rate_limits[client_ip] = []
    _telemetry_rate_limits[client_ip].append(now)


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
    current_user: Optional[TokenInfo] = Depends(get_optional_user)
) -> TelemetryResponse:
    """
    Submit client-side response time data.
    
    Data is stored for analysis of client-perceived performance.
    """
    client_ip = request.client.host if request.client else "unknown"
    
    if not _check_rate_limit(client_ip):
        logger.warning(f"Telemetry submission rate limit exceeded for {client_ip}")
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"error": {"code": 429, "message": "Telemetry submission rate limit exceeded"}}
        )
    
    user_id = current_user.user_id if current_user else None

    try:
        from src.api import get_telemetry
        telemetry = get_telemetry()
        
        if not telemetry:
            # Silently ignore if telemetry is disabled, but still count against rate limit
            logger.info(f"Telemetry submission received from {client_ip} (user: {user_id}) but telemetry module is not available")
            _record_submission(client_ip)
            return TelemetryResponse(
                accepted=0,
                message="Telemetry module not available"
            )
            
        accepted_count = 0
        try:
            for entry in submission.entries:
                telemetry.record_response_time(
                    endpoint=entry.endpoint,
                    method=entry.method,
                    response_time_ms=entry.response_time_ms,
                    status_code=entry.status_code,
                    user_id=user_id,
                    timestamp=entry.timestamp
                )
                accepted_count += 1
        except Exception as te:
            logger.error(f"Error recording telemetry entries from {client_ip} (user: {user_id}): {te}", exc_info=True)
            # Continue to return what we have
            
        _record_submission(client_ip)
        
        if accepted_count > 0:
            logger.debug(f"Accepted {accepted_count} telemetry entries from {client_ip} (user: {user_id})")
        
        return TelemetryResponse(
            accepted=accepted_count,
            message=f"Successfully accepted {accepted_count} telemetry entries"
        )
    except Exception as e:
        logger.error(f"Failed to process telemetry submission from {client_ip} (user: {user_id}): {e}", exc_info=True)
        # Return success to client even if recording fails (don't break client app)
        return TelemetryResponse(
            accepted=0,
            message="Telemetry processing failed"
        )
