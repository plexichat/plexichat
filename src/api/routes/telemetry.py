"""
Telemetry API routes.

Handles client-submitted response time telemetry data.
"""

from fastapi import APIRouter, Depends, HTTPException, status, Request
from typing import Any, Optional
import hashlib
import json
import re

import utils.config as config
import utils.logger as logger
from src.api.dependencies import get_optional_user
from src.api.request_guards import read_body_capped
from src.core.auth.models import TokenInfo
from src.api.schemas.telemetry import TelemetrySubmission, TelemetryResponse
from src.api.schemas.common import ErrorResponse
from src.core import ratelimit
from src.utils.net import get_client_ip


router = APIRouter(prefix="/telemetry", tags=["Telemetry"])

# CSP reports are unauthenticated browser callbacks. Keep malformed or hostile
# clients from forcing an unbounded request body allocation.
_MAX_CSP_REPORT_BYTES = 64 * 1024
# Redacted URIs and any other logged string are capped so a hostile report
# cannot flood the log with oversized content.
_MAX_CSP_URI_CHARS = 512
_MAX_CSP_LOG_CHARS = 4096
# Client telemetry flushes carry at most 100 small entries; anything larger is
# abuse. Check the raw body length as well as the parsed entry count.
_MAX_TELEMETRY_BODY_BYTES = 256 * 1024


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
    # Key the bucket on the trusted-proxy-aware client IP, exactly as every
    # other rate-limited route does. Using the raw socket peer made every
    # client behind a reverse proxy share one deployment-wide telemetry
    # bucket, so one noisy client could 429 everyone.
    client_ip = get_client_ip(request) or (
        request.client.host if request.client else "unknown"
    )

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

    # Enforce the raw-body ceiling before the broad try/except below so an
    # oversized flush returns 413 instead of being swallowed as a soft failure.
    await read_body_capped(request, _MAX_TELEMETRY_BODY_BYTES)

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


# A CSP report is an attacker-controlled JSON document that is written straight
# to the application log. Two fields of it are dangerous even after the old
# length cap: URL-valued fields (``blocked-url`` / ``blocked-uri`` are the URL of
# the resource that triggered the violation and commonly carry query-string
# credentials) and any string containing C0/DEL controls (a newline in
# ``script-sample`` forges an extra log line). The previous sanitizer only
# matched ``*-uri`` and ``referrer``, so ``blocked-url`` was logged verbatim and
# no field had its control characters escaped (CWE-117/CWE-532).
_CSP_URL_KEY_SUFFIXES = ("-uri", "-url")
_CSP_URL_KEYS = frozenset({"referrer", "source-file", "source_file"})
_LOG_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


def _safe_log_string(value: str) -> str:
    """Cap a log string and escape control characters before it reaches a log."""
    text = str(value)[:_MAX_CSP_URI_CHARS]
    return _LOG_CONTROL_RE.sub(lambda match: f"\\x{ord(match.group(0)):02x}", text)


def _redact_uri(value: str) -> str:
    """Strip query/fragment (which can embed tokens), then make the value log-safe."""
    return _safe_log_string(str(value).split("#", 1)[0].split("?", 1)[0])


def _sanitize_csp_report(report: Any) -> Any:
    """
    Redact attacker-controlled URIs/log-forcing characters before logging.

    URL-bearing fields have their query and fragment stripped; *every* string
    is length-capped and has C0/DEL controls escaped so one report cannot forge
    additional log lines. Nested dicts/lists are sanitized recursively.
    """
    if isinstance(report, dict):
        sanitized = {}
        for key, value in report.items():
            lower_key = str(key).lower()
            if isinstance(value, str) and (
                lower_key in _CSP_URL_KEYS or lower_key.endswith(_CSP_URL_KEY_SUFFIXES)
            ):
                sanitized[key] = _redact_uri(value)
            else:
                sanitized[key] = _sanitize_csp_report(value)
        return sanitized
    if isinstance(report, list):
        return [_sanitize_csp_report(item) for item in report]
    if isinstance(report, str):
        return _safe_log_string(report)
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
