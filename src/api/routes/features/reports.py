import asyncio
import json
import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from starlette.concurrency import run_in_threadpool
from typing import Optional, List

import utils.logger as logger
import src.api as api
from src.utils.encryption import generate_snowflake_id
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.common import ErrorResponse, SnowflakeIDParam
from src.core.security.hardening import (
    HardeningViolation,
    reject_control_characters,
    require_http_url,
)
from src.core.security.hardening.text import strip_control_characters
from src.core.events.snowflakes import stringify_snowflake_ids
from src.api.routes.users.helpers import require_user_token
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
VALID_REPORT_PRIORITIES = {"low", "medium", "high", "critical"}
VALID_REPORT_STATUSES = {"open", "investigating", "resolved", "dismissed", "escalated"}
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


def _message_report_context(conversation_id) -> tuple[Optional[int], Optional[int]]:
    """Resolve the authoritative ``(channel_id, server_id)`` for a message report.

    ``EnhancedReportRequest.channel_id`` / ``server_id`` are caller-supplied and
    therefore untrusted. Proving the reporter can view a message (via
    ``get_message``) says nothing about which channel/server the report should
    be attributed to. Persisting the client values verbatim let a reporter
    attribute a report to an arbitrary channel or server — including one they
    are not a member of — poisoning moderation-queue context and misleading
    moderators. The context is resolved from the server-authoritative
    conversation the reported message actually lives in; if it cannot be
    resolved (DMs, missing servers module), both values are NULL.

    This never raises on malformed server data: a bad row must fail closed to
    "no context" rather than block a legitimate report or leak internals.
    """
    if conversation_id is None:
        return None, None
    try:
        servers_mod = api.get_servers()
    except Exception:
        servers_mod = None
    if servers_mod is None:
        return None, None
    lookup = getattr(servers_mod, "get_channel_by_conversation", None)
    if lookup is None:
        return None, None
    try:
        info = lookup(int(conversation_id))
    except Exception:
        return None, None
    if not isinstance(info, dict):
        return None, None

    def _valid_id(value) -> Optional[int]:
        if value is None:
            return None
        try:
            return parse_id(str(value), "report context ID")
        except HTTPException:
            return None

    return _valid_id(info.get("id")), _valid_id(info.get("server_id"))


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

        from src.core.security.outbound import reject_forbidden_ip_literal

        parsed = urlparse(url.strip())
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            return False
        if parsed.username or parsed.password:
            return False
        # A literal backslash in the authority is a parser differential:
        # ``urlparse`` keeps it in ``netloc`` and derives the host from the text
        # to the RIGHT of an ``@``, but http(s) clients (requests/urllib3 and
        # browsers) follow the WHATWG rule and read ``\`` as ``/``, connecting to
        # the address on the LEFT. Without this check
        # ``http://169.254.169.254\@public.example/x`` is validated as the
        # public host but fetched at the cloud metadata address. A literal
        # backslash has no legitimate place in a URL authority (one meant as
        # data must be percent-encoded, which keeps it out of the authority).
        # Mirrors outbound.parse_outbound_url and web_push._require_public_endpoint.
        if "\\" in (parsed.netloc or ""):
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
        # Delegate the IP-range policy to the central SSRF gate so this
        # submission path applies exactly the same non-public rules as the
        # fetchers that later consume these evidence URLs. A hand-rolled
        # ``is_private`` check let RFC 6598 CGNAT space (100.64.0.0/10) and
        # its IPv4-mapped IPv6 form through -- ranges the shared validator
        # explicitly blocks because they route to non-public infrastructure.
        for addr in addresses:
            reject_forbidden_ip_literal(str(addr), "evidence_url")
        return bool(addresses)
    except Exception:
        return False


async def _is_safe_url_async(url: str) -> bool:
    """Async wrapper around `_is_safe_url` that keeps DNS off the event loop.

    `_is_safe_url` calls `socket.getaddrinfo`, a blocking syscall bounded by
    the resolver timeout. It is reached from the `async def` report handler
    with a client-supplied evidence URL, so a request naming a slow-resolving
    host stalled the whole worker. Same fix as the media proxy: run it in the
    threadpool.
    """
    return await run_in_threadpool(_is_safe_url, url)


# Feedback/report payloads are stored verbatim and copied into moderator UIs.
# Bound the human-authored fields and enumerate the machine-chosen ones so an
# unbounded category or reason cannot poison the queue or the rendering path.
MAX_REASON_LENGTH = 1000
MAX_DETAILS_LENGTH = 2000
MAX_RESOLUTION_LENGTH = 2000
MAX_EVIDENCE_URLS = 5
MAX_EVIDENCE_URL_LENGTH = 2048
# Fixed upper bound of concurrent DNS lookups for evidence-URL validation. The
# URL count is itself capped by MAX_EVIDENCE_URLS, so the semaphore bounds
# in-flight work without an unbounded task explosion.
_EVIDENCE_URL_CONCURRENCY = 4
# Plaintext preview of the reported message copied into the moderation queue.
MAX_MESSAGE_CONTENT_PREVIEW = 500


def _safe_message_content(content) -> Optional[str]:
    """Return a bounded, control-free preview of a reported message.

    ``message_content`` is persisted in the reports table and later rendered in
    moderator/admin UIs and log lines. The plain report route sanitises this
    field through ``_bounded_report_field`` (which rejects control characters);
    the enhanced route previously sliced the raw message content instead, so a
    reported message containing a C0 control (e.g. ESC / NUL), a C1 or Unicode
    NEL line break, or a U+202E bidi override was stored verbatim. That lets a
    reporter forge a line in a moderation log or disguise a URL/name when the
    row is rendered to a moderator -- the exact parser differential the shared
    hardening gate exists to close. Strip the same character set, then bound the
    preview so a single report cannot stuff an arbitrarily large body here.
    """
    if not isinstance(content, str) or not content:
        return None
    cleaned = strip_control_characters(content)
    return cleaned[:MAX_MESSAGE_CONTENT_PREVIEW] or None


def _bounded_text(value, field: str, maximum: int) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > maximum:
        raise ValueError(f"{field} is too long")
    try:
        return reject_control_characters(value, field)
    except HardeningViolation as exc:
        raise ValueError(str(exc)) from exc


class EnhancedReportRequest(BaseModel):
    target_type: str = Field(..., description="'message' or 'user'")
    target_id: str = Field(..., description="ID of the reported message or user")
    reason: str = Field(
        ..., min_length=1, max_length=MAX_REASON_LENGTH, description="Report reason"
    )
    category: str = Field("other", description="Report category")
    priority: str = Field("medium", description="Priority: low, medium, high, critical")
    details: Optional[str] = Field(
        None, max_length=MAX_DETAILS_LENGTH, description="Additional details"
    )
    evidence_urls: Optional[List[str]] = Field(
        None, max_length=MAX_EVIDENCE_URLS, description="URLs of evidence"
    )
    channel_id: Optional[str] = Field(
        None, description="Channel ID (for message reports)"
    )
    server_id: Optional[str] = Field(
        None, description="Server ID (for message reports)"
    )

    @field_validator("target_type")
    @classmethod
    def _valid_target_type(cls, value: str, info):
        if value not in ("message", "user"):
            raise ValueError("target_type must be 'message' or 'user'")
        return value

    @field_validator("target_id")
    @classmethod
    def _valid_target_id(cls, value: str, info):
        try:
            return str(parse_id(value, info.field_name))
        except (HardeningViolation, HTTPException) as exc:
            raise ValueError("Invalid identifier") from exc

    @field_validator("category")
    @classmethod
    def _valid_category(cls, value: str, info):
        if value not in VALID_REPORT_CATEGORIES:
            raise ValueError("Invalid report category")
        return value

    @field_validator("priority")
    @classmethod
    def _valid_priority(cls, value: str, info):
        if value not in VALID_REPORT_PRIORITIES:
            raise ValueError("Invalid priority")
        return value

    @field_validator("reason")
    @classmethod
    def _safe_reason(cls, value: str, info):
        return _bounded_text(value, info.field_name, MAX_REASON_LENGTH)

    @field_validator("details")
    @classmethod
    def _safe_details(cls, value, info):
        return _bounded_text(value, info.field_name, MAX_DETAILS_LENGTH)

    @field_validator("evidence_urls")
    @classmethod
    def _safe_evidence_urls(cls, value, info):
        if value is None:
            return value
        if len(value) > MAX_EVIDENCE_URLS:
            raise ValueError("Too many evidence URLs")
        checked = []
        for raw in value:
            if not isinstance(raw, str) or len(raw) > MAX_EVIDENCE_URL_LENGTH:
                raise ValueError("Invalid evidence URL")
            try:
                checked.append(require_http_url(raw, info.field_name))
            except HardeningViolation as exc:
                raise ValueError(str(exc)) from exc
        return checked


class ReportStatusUpdateRequest(BaseModel):
    status: str = Field(
        ...,
        description="New status: open, investigating, resolved, dismissed, escalated",
    )
    priority: Optional[str] = Field(None, description="Updated priority")
    assigned_to: Optional[str] = Field(None, description="Admin user ID to assign")
    admin_notes: Optional[str] = Field(
        None, max_length=MAX_RESOLUTION_LENGTH, description="Admin notes"
    )
    resolution: Optional[str] = Field(
        None, max_length=MAX_RESOLUTION_LENGTH, description="Resolution description"
    )

    @field_validator("status")
    @classmethod
    def _valid_status(cls, value: str, info):
        if value not in VALID_REPORT_STATUSES:
            raise ValueError("Invalid report status")
        return value

    @field_validator("priority")
    @classmethod
    def _valid_priority(cls, value, info):
        if value is None:
            return value
        if value not in VALID_REPORT_PRIORITIES:
            raise ValueError("Invalid priority")
        return value

    @field_validator("assigned_to")
    @classmethod
    def _valid_assignee(cls, value, info):
        if value is None:
            return value
        try:
            return str(parse_id(value, info.field_name))
        except (HardeningViolation, HTTPException) as exc:
            raise ValueError("Invalid identifier") from exc

    @field_validator("admin_notes", "resolution")
    @classmethod
    def _safe_text(cls, value, info):
        return _bounded_text(value, info.field_name, MAX_RESOLUTION_LENGTH)


@router.post(
    "/reports/enhanced",
    summary="Submit enhanced report",
    responses={400: {"model": ErrorResponse}, 401: {"model": ErrorResponse}},
)
async def submit_enhanced_report(
    body: EnhancedReportRequest, current_user: TokenInfo = Depends(get_current_user)
):
    require_user_token(current_user)
    _check_enhanced_report_rate_limit(current_user.user_id)

    if body.priority not in VALID_REPORT_PRIORITIES:
        raise_bad_request(
            f"Invalid priority. Must be one of: {', '.join(sorted(VALID_REPORT_PRIORITIES))}"
        )

    # REP-4: only supported target types are accepted
    if body.target_type not in ("message", "user"):
        raise_bad_request("target_type must be 'message' or 'user'")
    if body.category not in VALID_REPORT_CATEGORIES:
        raise_bad_request("Invalid report category")

    db: DatabaseProtocol | None = api.get_db()
    if not db:
        raise_internal("Database not available")

    target_id = parse_id(body.target_id, "target ID")

    # REP-5: evidence URLs must be well-formed http(s) links. Each check runs
    # a DNS lookup in the threadpool; awaiting them one at a time made wall
    # time scale as N * resolver-latency. Run the independent checks under a
    # fixed-size semaphore so they overlap while bounding in-flight work.
    # Fail-closed: any unsafe URL rejects the whole report.
    if body.evidence_urls:
        sem = asyncio.Semaphore(_EVIDENCE_URL_CONCURRENCY)

        async def _check_url(url: str) -> bool:
            async with sem:
                return await _is_safe_url_async(url)

        results = await asyncio.gather(*(_check_url(url) for url in body.evidence_urls))
        if not all(results):
            # Never reflect the rejected URL into the error body: it is
            # untrusted input that may later be rendered verbatim in a
            # moderation UI (reflected-content / XSS sink).
            raise_bad_request("Invalid evidence URL")

    now = int(time.time() * 1000)
    report_id = generate_snowflake_id()

    reported_user_id = None
    message_content = None
    channel_id = None
    server_id = None

    if body.target_type == "message":
        # REP-7: channel_id/server_id are derived from the server-side
        # conversation, never from the request body. The reporter only proves
        # they can view the message; the client values were previously stored
        # verbatim, allowing a report to be planted in any moderation context.
        # Validate any supplied values for strictness, but do not persist them.
        if body.channel_id is not None:
            parse_id(body.channel_id, "channel ID")
        if body.server_id is not None:
            parse_id(body.server_id, "server ID")
        messaging = api.get_messaging()
        msg = None
        if messaging:
            try:
                msg = messaging.get_message(current_user.user_id, target_id)
            except Exception:
                logger.warning("Failed to fetch message %s for report", target_id)
        if not msg:
            raise_bad_request("Message not found")
        channel_id, server_id = _message_report_context(
            getattr(msg, "conversation_id", None)
        )
        reported_user_id = msg.author_id
        # REP-3: reporters may not report their own content
        if reported_user_id == current_user.user_id:
            raise_bad_request("You cannot report your own message")
        message_content = _safe_message_content(msg.content)
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


def _gate_flag(row, key: str, index: int):
    """Read a scalar column from a dict- or tuple-shaped DB row."""
    if row is None:
        return None
    if isinstance(row, dict):
        return row.get(key)
    return row[index] if len(row) > index else None


@router.patch(
    "/reports/{report_id}/status",
    summary="Update report status (admin)",
    responses={
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
    },
)
async def update_report_status(
    report_id: SnowflakeIDParam,
    body: ReportStatusUpdateRequest,
    current_user: TokenInfo = Depends(get_current_user),
):
    # This endpoint mutates moderation state (status, priority, assignment,
    # resolution). A bot token carries its owner's ``user_id``, so the admin
    # membership gate below would otherwise let an admin-owned bot perform
    # those writes. Every other handler in this router is bound to a human
    # account via require_user_token; apply the same rule here.
    require_user_token(current_user)

    db: DatabaseProtocol | None = api.get_db()
    if not db:
        raise_internal("Database not available")

    rid = report_id

    # Resolve both gate checks in a single round-trip. Ordering is preserved:
    # a non-admin gets 403 before any request validation, and a missing report
    # is 404 only after validation succeeds.
    gate = db.fetch_one(
        "SELECT (SELECT 1 FROM admin_users WHERE id = ?) AS is_admin, "
        "(SELECT 1 FROM reports WHERE id = ?) AS report_exists",
        (current_user.user_id, rid),
    )
    if not _gate_flag(gate, "is_admin", 0):
        raise_forbidden("Admin access required")

    # Membership in admin_users is not enough to mutate moderation state.
    # Admin accounts are scoped by role: every sibling admin report action in
    # moderation.py requires reports.action, and the read path in this same
    # router already requires reports.read for admins. Without this check a
    # tickets-only support admin (or any role without report scopes) could
    # change a report's status, priority, assignment, resolution and internal
    # notes simply by virtue of being in the admin table. Fail closed when the
    # permission subsystem cannot resolve the caller's grants.
    from src.core.admin.permissions import check_admin_permission

    if not check_admin_permission(current_user.user_id, "reports.action", db):
        raise_forbidden("Insufficient permissions")

    if body.status not in VALID_REPORT_STATUSES:
        raise_bad_request(
            f"Invalid status. Must be one of: {', '.join(sorted(VALID_REPORT_STATUSES))}"
        )

    # Priority is validated on submit; validate it here too so a status
    # update cannot store an arbitrary string in the moderation queue.
    if body.priority is not None and body.priority not in VALID_REPORT_PRIORITIES:
        raise_bad_request(
            f"Invalid priority. Must be one of: {', '.join(sorted(VALID_REPORT_PRIORITIES))}"
        )

    # The response must reflect persisted state: updating a report that does
    # not exist previously returned success with the requested status because
    # the UPDATE matched no rows. Report the stored state honestly (404).
    if not _gate_flag(gate, "report_exists", 1):
        raise_not_found("Report not found")

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
    "/reports/{report_id}",
    summary="Get report details",
    responses={401: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
)
async def get_report_details(
    report_id: SnowflakeIDParam, current_user: TokenInfo = Depends(get_current_user)
):
    require_user_token(current_user)
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
    # Bare membership in admin_users is not sufficient for full report
    # visibility. Admin accounts are scoped by role, and every sibling admin
    # report listing requires reports.read: without that scope an admin whose
    # roles grant no report permissions (e.g. a tickets-only support admin)
    # could read any report here -- including the reporter's identity and the
    # plaintext preview of a message from a server they are not in.
    from src.core.admin.permissions import check_admin_permission

    is_admin = bool(
        db.fetch_one("SELECT id FROM admin_users WHERE id = ?", (current_user.user_id,))
        and check_admin_permission(current_user.user_id, "reports.read", db)
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

    return {"report": stringify_snowflake_ids(data)}
