"""
Telemetry API routes.

Handles client-submitted response time telemetry data.
"""

from fastapi import APIRouter, Depends, HTTPException, status, Request
from pydantic import BaseModel, Field
from typing import List, Optional, Any
import time

import utils.config as config
import utils.logger as logger
from src.api.dependencies import get_optional_user, get_db
from src.core.auth.models import TokenInfo


router = APIRouter(prefix="/telemetry", tags=["telemetry"])


class ResponseTimeEntry(BaseModel):
    """A single response time measurement."""

    endpoint: str = Field(..., max_length=255)
    method: str = Field(..., max_length=10)
    response_time_ms: float = Field(..., ge=0)
    status_code: int = Field(..., ge=100, le=599)
    timestamp: Optional[int] = None


class TelemetrySubmission(BaseModel):
    """Batch submission of response time data."""

    entries: List[ResponseTimeEntry] = Field(..., max_length=100)


class TelemetryResponse(BaseModel):
    """Response for telemetry submission."""

    accepted: int
    message: str


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


@router.post("/response-times", response_model=TelemetryResponse)
async def submit_response_times(
    submission: TelemetrySubmission,
    request: Request,
    current_user: Optional[TokenInfo] = Depends(get_optional_user),
    db: Optional[Any] = Depends(get_db),
):
    """
    Submit anonymized response time telemetry data.

    Clients can batch up to 100 entries per submission.
    Rate limited to prevent abuse.
    """
    # Check if telemetry collection is enabled
    if not config.get("telemetry.enabled", True):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Telemetry collection is currently disabled",
        )

    # Get client IP for rate limiting
    client_ip = request.client.host if request.client else "unknown"

    # Check rate limit
    if not _check_rate_limit(client_ip):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many telemetry submissions. Please try again later.",
        )

    # Import telemetry module
    try:
        from src.core import telemetry

        if not telemetry.is_setup():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Telemetry system not initialized",
            )
    except ImportError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Telemetry module not available",
        )

    # Generate anonymized client ID (hash of IP + user agent)
    import hashlib

    user_agent = request.headers.get("user-agent", "")
    client_id = hashlib.sha256(f"{client_ip}:{user_agent}".encode()).hexdigest()[:16]

    # Convert entries to dict format
    entries = [
        {
            "endpoint": e.endpoint,
            "method": e.method.upper(),
            "response_time_ms": e.response_time_ms,
            "status_code": e.status_code,
            "timestamp": e.timestamp or int(time.time() * 1000),
        }
        for e in submission.entries
    ]

    # Submit to telemetry module
    accepted = telemetry.submit_response_times(entries, client_id)

    # Record for rate limiting
    _record_submission(client_ip)

    logger.debug(
        f"Telemetry: accepted {accepted}/{len(entries)} entries from {client_id}"
    )

    return TelemetryResponse(
        accepted=accepted,
        message=f"Accepted {accepted} of {len(submission.entries)} entries",
    )
