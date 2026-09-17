"""
Telemetry API routes.

Handles client-submitted response time telemetry data.
"""

from fastapi import APIRouter, Depends, HTTPException, status, Request
from typing import Any, Optional
import hashlib
import json

import utils.config as config
import utils.logger as logger
from src.api.dependencies import get_optional_user
from src.core.auth.models import TokenInfo
from src.api.schemas.telemetry import TelemetrySubmission, TelemetryResponse
from src.api.schemas.common import ErrorResponse
from src.core import ratelimit


router = APIRouter(prefix="/telemetry", tags=["Telemetry"])

# CSP reports are unauthenticated browser callbacks. Keep malformed or hostile
# clients from forcing an unbounded request body allocation.
_MAX_CSP_REPORT_BYTES = 64 * 1024
# Redacted URIs and any other logged string are capped so a hostile report
# cannot flood the log with oversized content.
_MAX_CSP_URI_CHARS = 512
_MAX_CSP_LOG_CHARS = 4096


def _anonymize_client_id(user_id: Any) -> Optional[str]:
    """
    Return a salted SHA-256 digest of the user id (never the raw id) so
    stored telemetry cannot be linked back to an authenticated identity.
    The salt is per-install via ``telemetry.client_hash_salt``.
    """
    salt = str(config.get("telemetry.client_hash_salt", "") or "")
    digest = hashlib.sha256(f"{salt}:{user_id}".encode()).hexdigest()[:32]
    return f"anon_{digest}"


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

    rl_result = await ratelimit.acheck_rate_limit(
        ip_address=client_ip,
        route="POST /telemetry",
        is_internal=getattr(request.state, "is_internal", False),
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
        from src.api import get_telemetry, is_setup

        # Telemetry is disabled-by-default; before API setup (and when the
        # module never initialized) submissions are accepted-but-dropped
        # quietly. Logging ERROR per flush here would turn every client
        # telemetry flush into log noise for a feature that is off.
        telemetry = get_telemetry() if is_setup() else None
        if not telemetry or not getattr(telemetry, "is_setup", lambda: True)():
            logger.debug(
                f"Telemetry submission from {client_ip} (user: {user_id}) "
                "ignored: telemetry module is disabled or not initialized"
            )
            return TelemetryResponse(
                accepted=0, message="Telemetry module not available"
            )

        accepted_count = 0
        try:
            # Map API schema to core module expected format. An omitted or
            # None timestamp is left out so the core defaults it to
            # server-now (documented) instead of dropping the entry.
            core_entries = []
            for entry in submission.entries:
                mapped: dict = {
                    "endpoint": entry.endpoint,
                    "method": entry.method,
                    "response_time_ms": entry.response_time_ms,
                    "status_code": entry.status_code,
                    "db_queries": entry.db_queries,
                    "db_time_ms": entry.db_time_ms,
                }
                if entry.timestamp is not None:
                    mapped["timestamp"] = entry.timestamp
                core_entries.append(mapped)

            accepted_count = telemetry.submit_response_times(
                entries=core_entries,
                client_id=_anonymize_client_id(user_id) if user_id else None,
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


def _redact_uri(value: str) -> str:
    """Strip query strings (which can embed session/access tokens) and cap length."""
    return str(value).split("?", 1)[0][:_MAX_CSP_URI_CHARS]


def _sanitize_csp_report(report: Any) -> Any:
    """
    Redact attacker-controlled URIs/referrers before logging: query strings
    are stripped and every string value is size-capped.
    """
    if isinstance(report, dict):
        sanitized = {}
        for key, value in report.items():
            lower_key = str(key).lower()
            if isinstance(value, str) and (
                lower_key.endswith("-uri") or lower_key == "referrer"
            ):
                sanitized[key] = _redact_uri(value)
            else:
                sanitized[key] = _sanitize_csp_report(value)
        return sanitized
    if isinstance(report, list):
        return [_sanitize_csp_report(item) for item in report]
    if isinstance(report, str):
        return report[:_MAX_CSP_URI_CHARS]
    return report


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

    The browser sends this data when a CSP violation occurs. Report content
    is redacted (query-string-free, truncated) before it reaches the logs.
    """
    try:
        # CSP reports use application/csp-report or application/json. Read the
        # stream with a hard cap because Content-Length is optional and can be
        # forged by chunked clients.
        body_parts = []
        body_size = 0
        async for chunk in request.stream():
            body_size += len(chunk)
            if body_size > _MAX_CSP_REPORT_BYTES:
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail="CSP report exceeds the maximum size",
                )
            body_parts.append(chunk)
        body = b"".join(body_parts)
        try:
            report = json.loads(body)
        except Exception:
            report = {"raw": body.decode(errors="ignore")}

        client_ip = request.client.host if request.client else "unknown"
        logger.info(
            "CSP report received from %s: %s",
            client_ip,
            json.dumps(_sanitize_csp_report(report))[:_MAX_CSP_LOG_CHARS],
        )

        # Optionally record this in telemetry. Like the submission route,
        # an uninitialized/disabled module is skipped quietly instead of
        # logging an ERROR per browser report.
        from src.api import get_telemetry, is_setup

        telemetry = get_telemetry() if is_setup() else None
        if (
            telemetry
            and getattr(telemetry, "is_setup", lambda: True)()
            and hasattr(telemetry, "record_csp_violation")
        ):
            telemetry.record_csp_violation(report, client_ip)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error processing CSP report: {e}")

    return None
