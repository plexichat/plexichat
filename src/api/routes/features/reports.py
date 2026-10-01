from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from typing import Optional, List

import utils.logger as logger
import src.api as api
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.common import ErrorResponse
from .common import (
    parse_id,
    raise_bad_request,
    raise_forbidden,
    raise_internal,
    raise_not_found,
)
from .protocol import DatabaseProtocol

router = APIRouter()

# Column order for each report table, used to map tuple rows (drivers that
# do not expose column keys) back into a dict. These must match the table
# definitions in ``src/core/reports/schema.py`` exactly; the previous single
# list described the ``reports`` table but was also applied to message_reports
# and user_reports rows, and mislabelled every field after ``priority``.
_REPORT_COLUMNS = (
    "id",
    "reporter_id",
    "report_type",
    "target_id",
    "channel_id",
    "server_id",
    "reason",
    "category",
    "details",
    "evidence_ids",
    "message_content",
    "reported_user_id",
    "status",
    "priority",
    "assigned_to",
    "admin_notes",
    "resolution",
    "resolved_at",
    "resolved_by",
    "reviewed_at",
    "reviewed_by",
    "escalated_at",
    "created_at",
    "updated_at",
)
_MESSAGE_REPORT_COLUMNS = (
    "id",
    "message_id",
    "channel_id",
    "server_id",
    "reporter_id",
    "reported_user_id",
    "reason",
    "category",
    "details",
    "message_content",
    "evidence_urls",
    "priority",
    "status",
    "reported_at",
    "reviewed_at",
    "reviewed_by",
    "admin_notes",
    "action_taken",
    "assigned_to",
    "escalated_at",
    "resolution",
)
_USER_REPORT_COLUMNS = (
    "id",
    "reported_user_id",
    "reporter_id",
    "reason",
    "category",
    "details",
    "evidence_message_ids",
    "evidence_urls",
    "priority",
    "status",
    "reported_at",
    "reviewed_at",
    "reviewed_by",
    "admin_notes",
    "action_taken",
    "assigned_to",
    "escalated_at",
    "resolution",
)

ENHANCED_REPORT_RATE_LIMIT = 10
ENHANCED_REPORT_RATE_WINDOW = 60
VALID_REPORT_CATEGORIES = {
    "harassment",
    "spam",
    "inappropriate",
    "illegal",
    "hate_speech",
    "threats",
    "impersonation",
    "other",
}


def _check_enhanced_report_rate_limit(user_id: int) -> None:
    """Atomically cap enhanced reports per user in the shared cache tier."""
    try:
        from src.core.database import check_rate_limit

        allowed, _remaining = check_rate_limit(
            f"reports:enhanced:{user_id}",
            ENHANCED_REPORT_RATE_LIMIT,
            ENHANCED_REPORT_RATE_WINDOW,
        )
    except Exception as exc:
        # A limiter failure must not silently reopen a report-flooding path.
        logger.error("Enhanced report rate limiter unavailable: %s", exc)
        raise HTTPException(
            status_code=503,
            detail={
                "error": {
                    "code": 503,
                    "message": "Report submission temporarily unavailable",
                }
            },
        )

    if not allowed:
        raise HTTPException(
            status_code=429,
            detail={
                "error": {
                    "code": 429,
                    "message": "Too many reports submitted. Please slow down.",
                }
            },
        )


def _is_safe_url(url: str) -> bool:
    """Return True only for public http(s) URLs.

    Evidence links are later fetched or rendered by trusted moderation tools;
    reject loopback, link-local, private, multicast, and metadata addresses to
    avoid turning report submission into an SSRF primitive.
    """
    try:
        import ipaddress
        import socket
        from urllib.parse import urlparse

        parsed = urlparse(url.strip())
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            return False
        if parsed.username or parsed.password:
            return False
        host = parsed.hostname.rstrip(".")
        try:
            addresses = [ipaddress.ip_address(host)]
        except ValueError:
            try:
                addresses = [
                    ipaddress.ip_address(info[4][0])
                    for info in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
                ]
            except OSError:
                return False
        return bool(addresses) and all(
            not (
                addr.is_private
                or addr.is_loopback
                or addr.is_link_local
                or addr.is_multicast
                or addr.is_reserved
                or addr.is_unspecified
            )
            for addr in addresses
        )
    except Exception:
        return False


class EnhancedReportRequest(BaseModel):
    target_type: str = Field(..., description="'message' or 'user'")
    target_id: str = Field(..., description="ID of the reported message or user")
    reason: str = Field(..., min_length=1, max_length=1000, description="Report reason")
    category: str = Field("other", description="Report category")
    priority: str = Field("medium", description="Priority: low, medium, high, critical")
    details: Optional[str] = Field(
        None, max_length=2000, description="Additional details"
    )
    evidence_urls: Optional[List[str]] = Field(
        None, description="URLs of evidence (screenshots, etc.)"
    )
    channel_id: Optional[str] = Field(
        None, description="Channel ID (for message reports)"
    )
    server_id: Optional[str] = Field(
        None, description="Server ID (for message reports)"
    )


class ReportStatusUpdateRequest(BaseModel):
    status: str = Field(
        ...,
        description="New status: open, investigating, resolved, dismissed, escalated",
    )
    priority: Optional[str] = Field(None, description="Updated priority")
    assigned_to: Optional[str] = Field(None, description="Admin user ID to assign")
    admin_notes: Optional[str] = Field(None, max_length=2000, description="Admin notes")
    resolution: Optional[str] = Field(
        None, max_length=2000, description="Resolution description"
    )


@router.post(
    "/reports/enhanced",
    summary="Submit enhanced report",
    responses={400: {"model": ErrorResponse}, 401: {"model": ErrorResponse}},
)
async def submit_enhanced_report(
    body: EnhancedReportRequest, current_user: TokenInfo = Depends(get_current_user)
):
    _check_enhanced_report_rate_limit(current_user.user_id)

    valid_priorities = {"low", "medium", "high", "critical"}
    if body.priority not in valid_priorities:
        raise_bad_request(
            f"Invalid priority. Must be one of: {', '.join(valid_priorities)}"
        )

    # REP-4: only supported target types are accepted
    if body.target_type not in ("message", "user"):
        raise_bad_request("target_type must be 'message' or 'user'")
    if body.category not in VALID_REPORT_CATEGORIES:
        raise_bad_request("Invalid report category")

    db: DatabaseProtocol | None = api.get_db()
    if not db:
        raise_internal("Database not available")

    import json
    from src.utils.encryption import generate_snowflake_id
    import time

    target_id = parse_id(body.target_id, "target ID")

    # REP-5: evidence URLs must be well-formed http(s) links
    if body.evidence_urls:
        for url in body.evidence_urls:
            if not _is_safe_url(url):
                raise_bad_request(f"Invalid evidence URL: {url}")

    now = int(time.time() * 1000)
    report_id = generate_snowflake_id()

    reported_user_id = None
    message_content = None
    channel_id = None
    server_id = None

    if body.target_type == "message":
        channel_id = (
            parse_id(body.channel_id, "channel ID") if body.channel_id else None
        )
        server_id = parse_id(body.server_id, "server ID") if body.server_id else None
        messaging = api.get_messaging()
        msg = None
        if messaging:
            try:
                msg = messaging.get_message(current_user.user_id, target_id)
            except Exception:
                logger.warning("Failed to fetch message %s for report", target_id)
        if not msg:
            raise_bad_request("Message not found")
        reported_user_id = msg.author_id
        # REP-3: reporters may not report their own content
        if reported_user_id == current_user.user_id:
            raise_bad_request("You cannot report your own message")
        message_content = msg.content[:500] if msg.content else None
    elif body.target_type == "user":
        reported_user_id = target_id
        # REP-4: the reported user must actually exist
        user_row = db.fetch_one(
            "SELECT id FROM auth_users WHERE id = ?", (reported_user_id,)
        )
        if not user_row:
            raise_bad_request("Reported user not found")
        # REP-3: reporters may not report themselves
        if reported_user_id == current_user.user_id:
            raise_bad_request("You cannot report yourself")

    # Return the existing open report for this reporter/target rather than
    # creating another moderation-queue entry. The stored priority/status
    # are returned so a duplicate submission cannot spoof the moderation
    # queue state by echoing the request body.
    existing = db.fetch_one(
        "SELECT id, priority, status FROM reports WHERE reporter_id = ? AND report_type = ? "
        "AND target_id = ? AND status IN ('open', 'investigating') "
        "ORDER BY created_at DESC LIMIT 1",
        (current_user.user_id, body.target_type, target_id),
    )
    if existing:
        if isinstance(existing, dict):
            existing_id = existing["id"]
            stored_priority = existing.get("priority") or body.priority
            stored_status = existing.get("status") or "open"
        else:
            existing_id = existing[0]
            stored_priority = (
                existing[1] if len(existing) > 1 and existing[1] else body.priority
            )
            stored_status = existing[2] if len(existing) > 2 and existing[2] else "open"
        return {
            "success": True,
            "report_id": str(existing_id),
            "status": stored_status,
            "priority": stored_priority,
            "message": "Report already submitted",
        }

    evidence_urls_str = json.dumps(body.evidence_urls) if body.evidence_urls else None

    try:
        db.execute(
            """INSERT INTO reports
               (id, reporter_id, report_type, target_id, channel_id, server_id,
                reason, category, details, evidence_ids, message_content,
                reported_user_id, status, priority, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, ?)""",
            (
                report_id,
                current_user.user_id,
                body.target_type,
                target_id,
                channel_id,
                server_id,
                body.reason,
                body.category,
                body.details,
                evidence_urls_str,
                message_content,
                reported_user_id,
                body.priority,
                now,
                now,
            ),
        )

        logger.info(
            f"Enhanced report {report_id} submitted by user {current_user.user_id}"
        )
        return {
            "success": True,
            "report_id": str(report_id),
            "status": "open",
            "priority": body.priority,
        }
    except Exception:
        # The partial unique index closes the check-then-insert race. If a
        # concurrent request won the insert, return that existing report as an
        # idempotent success rather than exposing a database error.
        existing = db.fetch_one(
            "SELECT id, priority, status FROM reports WHERE reporter_id = ? AND report_type = ? "
            "AND target_id = ? AND status IN ('open', 'investigating') "
            "ORDER BY created_at DESC LIMIT 1",
            (current_user.user_id, body.target_type, target_id),
        )
        if existing:
            if isinstance(existing, dict):
                existing_id = existing["id"]
                stored_priority = existing.get("priority") or body.priority
                stored_status = existing.get("status") or "open"
            else:
                existing_id = existing[0]
                stored_priority = (
                    existing[1] if len(existing) > 1 and existing[1] else body.priority
                )
                stored_status = (
                    existing[2] if len(existing) > 2 and existing[2] else "open"
                )
            return {
                "success": True,
                "report_id": str(existing_id),
                "status": stored_status,
                "priority": stored_priority,
                "message": "Report already submitted",
            }
        logger.error("Failed to submit enhanced report", exc_info=True)
        raise_internal("Internal server error")


@router.patch(
    "/reports/{report_id}/status",
    summary="Update report status (admin)",
    responses={
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
    },
)
async def update_report_status(
    report_id: str,
    body: ReportStatusUpdateRequest,
    current_user: TokenInfo = Depends(get_current_user),
):
    db: DatabaseProtocol | None = api.get_db()
    if not db:
        raise_internal("Database not available")

    admin_row = db.fetch_one(
        "SELECT id FROM admin_users WHERE id = ?",
        (current_user.user_id,),
    )
    if not admin_row:
        raise_forbidden("Admin access required")

    rid = parse_id(report_id, "report ID")

    valid_statuses = {"open", "investigating", "resolved", "dismissed", "escalated"}
    if body.status not in valid_statuses:
        raise_bad_request(
            f"Invalid status. Must be one of: {', '.join(valid_statuses)}"
        )

    import time

    now = int(time.time() * 1000)

    # Static, fully-parameterized UPDATE. Every updatable column is present as a
    # COALESCE so only the fields explicitly provided in the request change;
    # omitted fields keep their current value. This avoids building SQL from
    # interpolated column names (bandit B608).
    is_review = body.status in ("investigating", "resolved", "dismissed", "escalated")

    # F-BUG-3: reopening a report must CLEAR the prior review/resolution
    # metadata — otherwise a report set back to "open" still displays as
    # reviewed/resolved by the previous admin. "open" is the only status that
    # resets the lifecycle fields; every other transition keeps them.
    # Escalation is lifecycle state too: a reopened report is no longer
    # escalated, so ``escalated_at`` is cleared exactly like the
    # resolution/review fields. Assignment, priority and admin notes are
    # report attributes (not lifecycle metadata) and are intentionally
    # retained across a reopen so the same admin can keep working it.
    is_reopen = body.status == "open"

    # Lifecycle values resolved BEFORE the UPDATE so the statement stays a
    # static, fully-parameterized string (bandit B608): each name below maps
    # to exactly one placeholder, in order. (A previous revision built a
    # ``params`` list and then referenced it positionally from the inline
    # tuple — a divergence trap; keep every value named from here on.)
    assigned_to = (
        parse_id(body.assigned_to, "assigned-to ID") if body.assigned_to else None
    )
    resolution = None if is_reopen else body.resolution
    resolved_at = None if is_reopen else (now if body.resolution else None)
    resolved_by = (
        None if is_reopen else (current_user.user_id if body.resolution else None)
    )
    escalated_at = None if is_reopen else (now if body.status == "escalated" else None)
    reviewed_at = None if is_reopen else (now if is_review else None)
    reviewed_by = None if is_reopen else (current_user.user_id if is_review else None)

    try:
        reopen_flag = 1 if is_reopen else 0
        db.execute(
            """UPDATE reports
               SET status = ?,
                   updated_at = ?,
                   priority = COALESCE(?, priority),
                   assigned_to = COALESCE(?, assigned_to),
                   admin_notes = COALESCE(?, admin_notes),
                   resolution = CASE WHEN ? = 1 THEN NULL ELSE COALESCE(?, resolution) END,
                   resolved_at = CASE WHEN ? = 1 THEN NULL ELSE COALESCE(?, resolved_at) END,
                   resolved_by = CASE WHEN ? = 1 THEN NULL ELSE COALESCE(?, resolved_by) END,
                   escalated_at = CASE WHEN ? = 1 THEN NULL ELSE COALESCE(?, escalated_at) END,
                   reviewed_at = CASE WHEN ? = 1 THEN NULL ELSE COALESCE(?, reviewed_at) END,
                   reviewed_by = CASE WHEN ? = 1 THEN NULL ELSE COALESCE(?, reviewed_by) END
               WHERE id = ?""",
            (
                body.status,
                now,
                body.priority,
                assigned_to,
                body.admin_notes,
                reopen_flag,
                resolution,
                reopen_flag,
                resolved_at,
                reopen_flag,
                resolved_by,
                reopen_flag,
                escalated_at,
                reopen_flag,
                reviewed_at,
                reopen_flag,
                reviewed_by,
                rid,
            ),
        )
        return {"success": True, "report_id": str(rid), "status": body.status}
    except Exception:
        logger.error("Failed to update report status", exc_info=True)
        raise_internal("Internal server error")


@router.get(
    "/reports/{report_id:int}",
    summary="Get report details",
    responses={401: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
)
async def get_report_details(
    report_id: int, current_user: TokenInfo = Depends(get_current_user)
):
    db: DatabaseProtocol | None = api.get_db()
    if not db:
        raise_internal("Database not available")

    rid = report_id

    # Each table has a different column order; keep the matching table's
    # column names alongside its row so a tuple row is labelled correctly.
    row = db.fetch_one("SELECT * FROM reports WHERE id = ?", (rid,))
    row_columns = _REPORT_COLUMNS
    if not row:
        row = db.fetch_one("SELECT * FROM message_reports WHERE id = ?", (rid,))
        row_columns = _MESSAGE_REPORT_COLUMNS
    if not row:
        row = db.fetch_one("SELECT * FROM user_reports WHERE id = ?", (rid,))
        row_columns = _USER_REPORT_COLUMNS
    if not row:
        raise_not_found("Report not found")

    if isinstance(row, dict):
        data = row
    else:
        data = dict(zip(row_columns, row))
    is_reporter = data.get("reporter_id") == current_user.user_id
    is_admin = db.fetch_one(
        "SELECT id FROM admin_users WHERE id = ?", (current_user.user_id,)
    )

    if not is_reporter and not is_admin:
        raise_forbidden("Access denied")

    # H3 FIX: reporters must not see internal moderation fields. Project to a
    # reporter-safe allowlist unless the viewer is an admin.
    if not is_admin:
        admin_only_fields = (
            "admin_notes",
            "reviewed_by",
            "reviewed_at",
            "action_taken",
            "resolution",
            "resolved_by",
            "assigned_to",
            "escalated_at",
        )
        data = {k: v for k, v in data.items() if k not in admin_only_fields}

    return {"report": data}
