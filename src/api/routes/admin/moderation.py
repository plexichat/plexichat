"""
Admin moderation and content review routes.
"""

from fastapi import APIRouter, Query, Request, HTTPException
from typing import List, Optional, Dict, Any, cast
from src.api.schemas.admin import (
    HashReportResponse,
    HashReportCountsResponse,
    HashReportReviewRequest,
    HashReportReviewResponse,
    MessageReportResponse,
    UserReportResponse,
    ModerationReportCountsResponse,
    ModerationReportReviewRequest,
    ModerationReportReviewResponse,
    BlockedHashResponse,
    ManualBlockHashRequest,
    BlockHashResponse,
    BlockedUserResponse,
    BlockUserRequest,
    BlockUserResponse,
    AutomodRuleResponse,
    AutomodRuleCreateRequest,
    AutomodRuleUpdateRequest,
    AutomodRuleAction,
    AutomodConfigResponse,
    AutomodConfigUpdateRequest,
)
from src.api.schemas.admin.automod import ActionTypeValue
from src.api.schemas.common import (
    ErrorResponse,
    SnowflakeIDParam,
    SuccessResponse,
)
from .utils import (
    check_host_restriction,
    # Kept for existing test doubles that patch this module attribute; every
    # read route now uses the combined scope gate instead of auth-only access.
    get_admin_from_token,  # noqa: F401
    require_admin_permission,
)
from ._summary_cache import get_or_compute
from src.core.security.hardening.text import strip_control_characters
from src.core.security.hardening import HardeningViolation, parse_id
import src.api as api
import utils.logger as logger
import utils.config as config

router = APIRouter()

# Short window for the read-only moderator queue/count summaries.  The scope
# gate runs before the cache is read, so a cached page is never served to a
# caller without the matching read scope.
_REPORTS_TTL_SECONDS = 2.0

# A page of reports references several user ids per row (reporter, reported
# user, assignee, reviewer). Resolving those with one ``SELECT ... WHERE id = ?``
# per report is the classic N+1 read. Bind at most this many ids per ``IN``
# statement so a large page costs a bounded number of round trips and never
# trips SQLite/Postgres parameter ceilings.
_BULK_USER_ID_CHUNK_SIZE = 500


def _iter_id_chunks(ids: List[int]):
    """Yield ``ids`` in bounded slices for ``IN (?, ...)`` binding."""
    for start in range(0, len(ids), _BULK_USER_ID_CHUNK_SIZE):
        yield ids[start : start + _BULK_USER_ID_CHUNK_SIZE]


def _bulk_usernames(db, ids: List[int]) -> Dict[int, Optional[str]]:
    """Resolve ``auth_users.id -> username`` in bounded ``IN`` chunks.

    The id set is built exclusively from user ids already present on the
    *authorized* report page, so this never widens the data a caller can see:
    the ``reports.read`` scope gate has already run before this helper is
    reached. Missing/deleted users are simply absent from the mapping. Any DB
    failure is swallowed so username enrichment can never fail an otherwise
    valid report listing.
    """
    unique_ids = sorted({int(value) for value in ids if value is not None})
    if not unique_ids:
        return {}
    mapping: Dict[int, Optional[str]] = {}
    for chunk in _iter_id_chunks(unique_ids):
        try:
            placeholders = ", ".join("?" for _ in chunk)
            rows = db.fetch_all(
                f"SELECT id, username FROM auth_users WHERE id IN ({placeholders})",
                tuple(chunk),
            )
        except Exception:  # pragma: no cover - enrichment is best effort
            continue
        for row in rows or []:
            if isinstance(row, dict):
                uid = row.get("id")
                username = row.get("username")
            else:
                uid = row[0] if len(row) > 0 else None
                username = row[1] if len(row) > 1 else None
            if uid is not None:
                mapping[int(uid)] = username
    return mapping


def _enrich_report_users(db, items: List[Dict[str, Any]]) -> None:
    """Attach username fields to a report page with O(ceil(n/500)) queries.

    Mutates only the freshly-built response dicts (never the cached DB rows).
    Each field is populated only when the corresponding id column exists on the
    row, so a legacy row shape is left untouched.
    """
    fields = (
        ("reporter_id", "reporter_username"),
        ("reported_user_id", "reported_username"),
        ("assigned_to", "assigned_to_username"),
        ("reviewed_by", "reviewed_by_username"),
    )
    ids: List[int] = []
    for item in items:
        for id_key, _ in fields:
            if id_key in item and item[id_key] is not None:
                try:
                    ids.append(int(item[id_key]))
                except (TypeError, ValueError):
                    continue
    if not ids:
        return
    usernames = _bulk_usernames(db, ids)
    for item in items:
        for id_key, name_key in fields:
            raw = item.get(id_key)
            if raw is None:
                continue
            try:
                uid = int(raw)
            except (TypeError, ValueError):
                continue
            if uid in usernames:
                item[name_key] = usernames[uid]


def _safe_label(value: Any) -> Any:
    """Strip control chars from a free-text status/category label."""
    if value is None:
        return None
    return strip_control_characters(value)


def _load_target_admin_context(
    db, admin_id: int, target_user_id: int
) -> tuple[bool, int, int]:
    """Resolve everything the admin-hierarchy gate needs in ONE query.

    The old flow probed ``admin_users`` and then called ``can_manage_admin``,
    which in turn calls ``get_admin_max_position`` twice -- three round trips
    per ban/unban.  Scalar subqueries keep the same decisions while collapsing
    that to a single statement (the two position lookups are independent, so
    there is nothing to gain from sequential reads).
    """
    row = db.fetch_one(
        """
        SELECT
            EXISTS(SELECT 1 FROM admin_users WHERE id = ?) AS target_exists,
            (SELECT COALESCE(MAX(r.position), 0)
               FROM admin_roles r
               JOIN admin_role_assignments a ON r.id = a.role_id
              WHERE a.admin_id = ?) AS current_pos,
            (SELECT COALESCE(MAX(r.position), 0)
               FROM admin_roles r
               JOIN admin_role_assignments a ON r.id = a.role_id
              WHERE a.admin_id = ?) AS target_pos
        """,
        (target_user_id, admin_id, target_user_id),
    )
    if not row:
        return False, 0, 0
    if isinstance(row, dict):
        exists = row.get("target_exists", False)
        current_pos = row.get("current_pos") or 0
        target_pos = row.get("target_pos") or 0
    else:
        exists = row[0]
        current_pos = row[1] if len(row) > 1 else 0
        target_pos = row[2] if len(row) > 2 else 0
    return bool(exists), int(current_pos), int(target_pos)


def _can_manage_admin_at(
    admin_id: int, target_user_id: int, current_pos: int, target_pos: int
) -> bool:
    """Same rule as ``can_manage_admin`` but using pre-fetched positions."""
    if int(admin_id) == int(target_user_id):
        return False
    return current_pos > target_pos


def _ensure_can_target_admin(admin_id: int, target_user_id: int) -> None:
    """Platform-wide bans must not bypass the admin hierarchy.

    If ``target_user_id`` is an admin account, the acting admin may only
    proceed when they can manage that admin per the role-position hierarchy.
    """
    db = api.get_db()
    if db is None:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Database not available"}},
        )
    exists, current_pos, target_pos = _load_target_admin_context(
        db, admin_id, target_user_id
    )
    if not exists:
        return
    if not _can_manage_admin_at(admin_id, target_user_id, current_pos, target_pos):
        raise HTTPException(
            status_code=403,
            detail={
                "error": {
                    "code": 403,
                    "message": "Insufficient role position to moderate this admin",
                }
            },
        )


def _get_reports_module():
    reports = api.get_reports()
    if not reports:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Reports module unavailable"}},
        )
    return reports


def _message_report_to_response(report) -> MessageReportResponse:
    return MessageReportResponse(
        id=str(report.id),
        message_id=str(report.message_id),
        channel_id=str(report.channel_id),
        server_id=str(report.server_id) if report.server_id is not None else None,
        reporter_id=str(report.reporter_id),
        reported_user_id=str(report.reported_user_id),
        reason=report.reason,
        category=report.category,
        details=report.details,
        message_content=report.message_content,
        status=report.status.value
        if hasattr(report.status, "value")
        else str(report.status),
        reported_at=report.reported_at,
        reviewed_at=report.reviewed_at,
        reviewed_by=str(report.reviewed_by) if report.reviewed_by is not None else None,
        admin_notes=report.admin_notes,
        action_taken=report.action_taken,
    )


def _user_report_to_response(report) -> UserReportResponse:
    return UserReportResponse(
        id=str(report.id),
        reported_user_id=str(report.reported_user_id),
        reporter_id=str(report.reporter_id),
        reason=report.reason,
        category=report.category,
        details=report.details,
        evidence_message_ids=[str(i) for i in (report.evidence_message_ids or [])],
        status=report.status.value
        if hasattr(report.status, "value")
        else str(report.status),
        reported_at=report.reported_at,
        reviewed_at=report.reviewed_at,
        reviewed_by=str(report.reviewed_by) if report.reviewed_by is not None else None,
        admin_notes=report.admin_notes,
        action_taken=report.action_taken,
    )


@router.get(
    "/hash-reports",
    response_model=List[HashReportResponse],
    responses={
        400: {"model": ErrorResponse, "description": "Invalid query parameters"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {"model": ErrorResponse, "description": "Insufficient permissions"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_hash_reports(
    request: Request,
    status_filter: Optional[str] = Query(None, max_length=32),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0, le=1000000),
):
    """
    Retrieve a list of content hash reports for review.

    Allows filtering by status and supports pagination.
    """
    check_host_restriction(request)
    require_admin_permission(request, "reports.read")
    from src.core import admin

    try:
        reports = admin.get_hash_reports(status_filter, limit, offset)
        return [
            HashReportResponse(
                id=str(r.id),
                hash_value=r.hash_value,
                phash_value=r.phash_value,
                reporter_id=str(r.reporter_id),
                reporter_username=r.reporter_username,
                reason=r.reason,
                details=r.details,
                status=r.status,
                reported_at=r.reported_at,
                reviewed_at=r.reviewed_at,
                reviewed_by=str(r.reviewed_by) if r.reviewed_by else None,
                admin_notes=r.admin_notes,
                uploader_id=str(r.uploader_id) if r.uploader_id else None,
                message_id=str(r.message_id) if r.message_id else None,
                attachment_url=r.attachment_url,
                block_uploader=r.block_uploader,
            )
            for r in reports
        ]
    except Exception as e:
        logger.error(f"Hash reports error: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.get(
    "/hash-reports/counts",
    response_model=HashReportCountsResponse,
    responses={
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {"model": ErrorResponse, "description": "Insufficient permissions"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_hash_report_counts(request: Request):
    """
    Get the total number of content hash reports, grouped by status.
    """
    check_host_restriction(request)
    require_admin_permission(request, "reports.read")
    from src.core import admin

    return HashReportCountsResponse(**admin.get_hash_report_counts())


@router.post(
    "/hash-reports/{report_id}/review",
    response_model=HashReportReviewResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {"model": ErrorResponse, "description": "Insufficient permissions"},
        404: {"model": ErrorResponse, "description": "Report not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def review_hash_report(
    report_id: SnowflakeIDParam,
    review: HashReportReviewRequest,
    request: Request,
):
    """
    Submit a review for a specific content hash report.

    Updates the report status and optionally takes action against the reported content.
    """
    check_host_restriction(request)
    admin_id = require_admin_permission(request, "reports.action")
    from src.core import admin

    if not admin.review_hash_report(report_id, admin_id, review.action, review.notes):
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": 404, "message": "Report not found"}},
        )
    return HashReportReviewResponse(success=True, action=review.action)


@router.get(
    "/message-reports",
    response_model=List[MessageReportResponse],
    responses={
        400: {"model": ErrorResponse, "description": "Invalid query parameters"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {"model": ErrorResponse, "description": "Insufficient permissions"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_message_reports(
    request: Request,
    status_filter: Optional[str] = Query(None, max_length=32),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0, le=1000000),
):
    """Retrieve message reports for admin review."""
    check_host_restriction(request)
    require_admin_permission(request, "reports.read")
    reports = _get_reports_module()

    try:
        return [
            _message_report_to_response(r)
            for r in reports.get_message_reports(status_filter, limit, offset)
        ]
    except Exception as e:
        logger.error(f"Message reports error: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.get(
    "/message-reports/counts",
    response_model=ModerationReportCountsResponse,
    responses={
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {"model": ErrorResponse, "description": "Insufficient permissions"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_message_report_counts(request: Request):
    """Get message report counts grouped by status."""
    check_host_restriction(request)
    require_admin_permission(request, "reports.read")
    return ModerationReportCountsResponse(
        **_get_reports_module().get_message_report_counts()
    )


@router.get(
    "/reports",
    responses={
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {"model": ErrorResponse, "description": "Insufficient permissions"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_enhanced_reports(
    request: Request,
    status_filter: Optional[str] = Query(None, max_length=32),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0, le=1_000_000),
):
    """List enhanced (``reports`` table) reports for moderator review.

    The enhanced submission pipeline persists to the ``reports`` table, which
    is separate from ``message_reports``/``user_reports``. Exposing it here
    keeps those reports visible in the moderator queue instead of orphaned.
    """
    check_host_restriction(request)
    require_admin_permission(request, "reports.read")

    def _load() -> dict:
        db = api.get_db()
        if db is None:
            raise HTTPException(
                status_code=500,
                detail={"error": {"code": 500, "message": "Database unavailable"}},
            )
        clauses = []
        params: List[Any] = []
        if status_filter:
            clauses.append("status = ?")
            params.append(status_filter)
        where = (" AND ".join(clauses)) if clauses else "1=1"
        # COUNT(*) OVER() gives the full filtered total alongside the page in a
        # single scan: the window is evaluated over the whole WHERE set before
        # LIMIT/OFFSET is applied.  This drops the separate COUNT round trip on
        # the hot path.  The status/created_at composite index (or the created_at
        # index when unfiltered) keeps the ordered scan index-backed.
        rows = db.fetch_all(
            f"SELECT *, COUNT(*) OVER() AS _report_total FROM reports WHERE {where} "
            "ORDER BY created_at DESC LIMIT ? OFFSET ?",
            tuple(params + [limit, offset]),
        )
        if rows and isinstance(rows[0], dict):
            total = int(rows[0]["_report_total"] or 0)
            # Never mutate the row dicts: fetch_all caches them, so popping the
            # helper column would corrupt every later identical request.
            items = [
                {k: v for k, v in row.items() if k != "_report_total"} for row in rows
            ]
        elif rows:
            total = int(rows[0][-1] or 0)
            items = [dict(row) for row in rows]
        elif offset:
            # A page beyond the end has no row to carry the window total; one
            # cheap COUNT keeps ``total`` accurate for that rare case.
            total_row = db.fetch_one(
                f"SELECT COUNT(*) AS count FROM reports WHERE {where}", tuple(params)
            )
            total = int(total_row["count"]) if total_row else 0
            items = []
        else:
            total = 0
            items = []
        # Status/category are rendered as filter chips and chart labels in the
        # moderator UI.  Normalize control characters out of those two fields
        # without touching the report body itself.
        for item in items:
            if "status" in item:
                item["status"] = _safe_label(item["status"])
            if "category" in item:
                item["category"] = _safe_label(item["category"])
        # Resolve the user ids referenced by this authorized page in bounded
        # IN chunks instead of one lookup per report (N+1). The scope gate has
        # already run; only ids from this page are queried, so no other
        # caller's data can be pulled in.
        _enrich_report_users(db, items)
        return {
            "items": items,
            "total": total,
            "has_more": offset + len(items) < total,
        }

    return get_or_compute(
        "reports_page",
        (status_filter, limit, offset),
        _load,
        _REPORTS_TTL_SECONDS,
    )


@router.get(
    "/reports/counts",
    responses={
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {"model": ErrorResponse, "description": "Insufficient permissions"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_enhanced_report_counts(request: Request):
    """Counts of enhanced reports grouped by status."""
    check_host_restriction(request)
    require_admin_permission(request, "reports.read")

    def _load() -> dict:
        db = api.get_db()
        if db is None:
            raise HTTPException(
                status_code=500,
                detail={"error": {"code": 500, "message": "Database unavailable"}},
            )
        counts: Dict[str, int] = {}
        # Bound the number of status buckets so a corrupt/crafted status value
        # cannot turn this aggregate into an unbounded response.
        rows = db.fetch_all(
            "SELECT status, COUNT(*) as count FROM reports GROUP BY status LIMIT 200"
        )
        total = 0
        for row in rows:
            status = _safe_label(row["status"])
            count = int(row["count"])
            counts[status] = count
            total += count
        counts["total"] = total
        return counts

    return get_or_compute("reports_counts", (), _load, _REPORTS_TTL_SECONDS)


@router.post(
    "/message-reports/{report_id}/review",
    response_model=ModerationReportReviewResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {"model": ErrorResponse, "description": "Insufficient permissions"},
        404: {"model": ErrorResponse, "description": "Report not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def review_message_report(
    report_id: SnowflakeIDParam,
    review: ModerationReportReviewRequest,
    request: Request,
):
    """Review a specific message report."""
    check_host_restriction(request)
    admin_id = require_admin_permission(request, "reports.action")
    reports = _get_reports_module()

    if not reports.review_message_report(
        report_id, admin_id, review.action, review.notes
    ):
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": 404, "message": "Report not found"}},
        )
    return ModerationReportReviewResponse(success=True, action=review.action)


@router.get(
    "/user-reports",
    response_model=List[UserReportResponse],
    responses={
        400: {"model": ErrorResponse, "description": "Invalid query parameters"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {"model": ErrorResponse, "description": "Insufficient permissions"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_user_reports(
    request: Request,
    status_filter: Optional[str] = Query(None, max_length=32),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0, le=1000000),
):
    """Retrieve user behavior reports for admin review."""
    check_host_restriction(request)
    require_admin_permission(request, "reports.read")
    reports = _get_reports_module()

    try:
        return [
            _user_report_to_response(r)
            for r in reports.get_user_reports(status_filter, limit, offset)
        ]
    except Exception as e:
        logger.error(f"User reports error: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.get(
    "/user-reports/counts",
    response_model=ModerationReportCountsResponse,
    responses={
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {"model": ErrorResponse, "description": "Insufficient permissions"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_user_report_counts(request: Request):
    """Get user report counts grouped by status."""
    check_host_restriction(request)
    require_admin_permission(request, "reports.read")
    return ModerationReportCountsResponse(
        **_get_reports_module().get_user_report_counts()
    )


@router.post(
    "/user-reports/{report_id}/review",
    response_model=ModerationReportReviewResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {"model": ErrorResponse, "description": "Insufficient permissions"},
        404: {"model": ErrorResponse, "description": "Report not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def review_user_report(
    report_id: SnowflakeIDParam,
    review: ModerationReportReviewRequest,
    request: Request,
):
    """Review a specific user report."""
    check_host_restriction(request)
    admin_id = require_admin_permission(request, "reports.action")
    reports = _get_reports_module()

    if not reports.review_user_report(report_id, admin_id, review.action, review.notes):
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": 404, "message": "Report not found"}},
        )
    return ModerationReportReviewResponse(success=True, action=review.action)


@router.get(
    "/blocked-hashes",
    response_model=List[BlockedHashResponse],
    responses={
        400: {"model": ErrorResponse, "description": "Invalid query parameters"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {"model": ErrorResponse, "description": "Insufficient permissions"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_blocked_hashes(
    request: Request,
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0, le=1000000),
):
    """
    List all currently blocked content hashes.
    """
    check_host_restriction(request)
    require_admin_permission(request, "blocked_hashes.read")
    from src.core import admin

    return [
        BlockedHashResponse(
            hash_value=h.hash_value,
            reason=h.reason,
            blocked_at=h.blocked_at,
            blocked_by=h.blocked_by,
            auto_blocked=h.auto_blocked,
            hash_type=h.hash_type,
            phash_threshold=h.phash_threshold,
        )
        for h in admin.get_blocked_hashes(limit, offset)
    ]


@router.post(
    "/blocked-hashes",
    response_model=BlockHashResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {"model": ErrorResponse, "description": "Insufficient permissions"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def block_hash_manually(block_request: ManualBlockHashRequest, request: Request):
    """
    Manually add a content hash to the blocklist.
    """
    check_host_restriction(request)
    admin_id = require_admin_permission(request, "admin.moderation.hashes")
    from src.core import admin

    hash_value = block_request.hash_value.strip().lower()
    hash_type = "phash" if len(hash_value) <= 32 else "sha256"
    valid_lengths = (16, 32) if hash_type == "phash" else (64,)
    if len(hash_value) not in valid_lengths or any(
        c not in "0123456789abcdef" for c in hash_value
    ):
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": f"Invalid {hash_type} hash"}},
        )
    phash_threshold = 10 if hash_type == "phash" else 0
    if not admin.block_hash(
        hash_value,
        block_request.reason,
        admin_id,
        hash_type,
        phash_threshold,
    ):
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Failed to block hash"}},
        )
    return BlockHashResponse(success=True, hash_value=hash_value, hash_type=hash_type)


@router.delete(
    "/blocked-hashes/{hash_value}",
    response_model=SuccessResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {"model": ErrorResponse, "description": "Insufficient permissions"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def unblock_hash(hash_value: str, request: Request):
    """
    Remove a content hash from the blocklist.
    """
    check_host_restriction(request)
    require_admin_permission(request, "admin.moderation.hashes")
    from src.core import admin

    if not admin.unblock_hash(hash_value):
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Failed to unblock hash"}},
        )
    return SuccessResponse(success=True, message=None)


@router.get(
    "/blocked-users",
    response_model=List[BlockedUserResponse],
    responses={
        400: {"model": ErrorResponse, "description": "Invalid query parameters"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {"model": ErrorResponse, "description": "Insufficient permissions"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_blocked_users(
    request: Request,
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0, le=1000000),
):
    """
    Retrieve a list of all currently blocked or banned users.
    """
    check_host_restriction(request)
    require_admin_permission(request, "blocked_users.read")
    from src.core import admin

    return [
        BlockedUserResponse(
            user_id=str(u.user_id),
            username=u.username,
            reason=u.reason,
            blocked_at=u.blocked_at,
            blocked_by=str(u.blocked_by) if u.blocked_by is not None else None,
            expires_at=u.expires_at,
        )
        for u in admin.get_blocked_users(limit, offset)
    ]


@router.post(
    "/blocked-users",
    response_model=BlockUserResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {"model": ErrorResponse, "description": "Insufficient permissions"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def block_user(block_request: BlockUserRequest, request: Request):
    """
    Block or ban a specific user from the platform.
    """
    check_host_restriction(request)
    admin_id = require_admin_permission(request, "admin.moderation.bans")
    from src.core import admin

    # A platform-wide ban is a privilege mutation. Bind the caller-supplied
    # user id through the strict ASCII snowflake gate before any hierarchy
    # check or write: the SnowflakeID schema rejects bool/coercible tokens on
    # the normal HTTP path, but a directly constructed body or internal caller
    # bypasses that validation. Bare ``int()`` laundered those tokens
    # (``True``->1, ``5.9``->5, ``"١٢٣"``->123, ``"1_2"``->12) into a DIFFERENT
    # target before the ban INSERT. parse_id fails closed with 400 so the
    # laundered id can never reach the hierarchy gate or admin.block_user.
    try:
        target_id = parse_id(block_request.user_id, "user_id")
    except (HardeningViolation, TypeError, ValueError):
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Invalid request data"}},
        )
    _ensure_can_target_admin(admin_id, target_id)
    if not admin.block_user(
        target_id,
        block_request.reason,
        admin_id,
        block_request.duration_hours,
    ):
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Failed to block user"}},
        )
    return BlockUserResponse(success=True, user_id=block_request.user_id)


@router.delete(
    "/blocked-users/{user_id}",
    response_model=SuccessResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {"model": ErrorResponse, "description": "Insufficient permissions"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def unblock_user(user_id: SnowflakeIDParam, request: Request):
    """
    Lift a block or ban from a specific user.
    """
    check_host_restriction(request)
    admin_id = require_admin_permission(request, "admin.moderation.bans")
    from src.core import admin

    target_id = int(user_id)
    _ensure_can_target_admin(admin_id, target_id)
    if not admin.unblock_user(target_id):
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Failed to unblock user"}},
        )
    return SuccessResponse(success=True, message=None)


def _rule_to_response(rule) -> AutomodRuleResponse:
    return AutomodRuleResponse(
        id=str(rule.id),
        server_id=str(rule.server_id),
        name=rule.name,
        rule_type=rule.rule_type.value
        if hasattr(rule.rule_type, "value")
        else str(rule.rule_type),
        enabled=bool(rule.enabled),
        config=rule.config,
        actions=[
            AutomodRuleAction(
                action_type=cast(
                    ActionTypeValue,
                    a.action_type.value
                    if hasattr(a.action_type, "value")
                    else str(a.action_type),
                ),
                duration_seconds=a.duration_seconds,
                reason=a.reason,
                notify_user=a.notify_user,
                metadata=a.metadata or {},
            )
            for a in (rule.actions or [])
        ],
        exempt_roles=[str(r) for r in (rule.exempt_roles or [])],
        exempt_channels=[str(c) for c in (rule.exempt_channels or [])],
        priority=rule.priority,
        check_all=bool(rule.check_all),
        created_at=rule.created_at,
        updated_at=rule.updated_at,
        created_by=str(rule.created_by),
    )


@router.get(
    "/automod/rules",
    response_model=List[AutomodRuleResponse],
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {"model": ErrorResponse, "description": "Insufficient permissions"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_automod_rules(request: Request, server_id: SnowflakeIDParam):
    """
    Retrieve the list of AutoMod rules configured for a specific server.
    """
    check_host_restriction(request)
    require_admin_permission(request, "automod.read")
    from src.core import automod

    try:
        rules = automod.get_server_rules(int(server_id))
        return [_rule_to_response(r) for r in rules]
    except Exception as e:
        logger.error(f"Automod rules error: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.post(
    "/automod/rules",
    response_model=AutomodRuleResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {"model": ErrorResponse, "description": "Insufficient permissions"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def create_automod_rule(body: AutomodRuleCreateRequest, request: Request):
    """
    Create a new AutoMod rule for a server.
    """
    check_host_restriction(request)
    admin_id = require_admin_permission(request, "admin.moderation.rules")
    from src.core import automod
    from src.core.automod.models import RuleType

    # Platform-admin rule creation writes a server-scoped rule. Bind the
    # caller-supplied server id through the strict ASCII snowflake gate before
    # any write: the SnowflakeID schema rejects bool/coercible tokens on the
    # normal HTTP path, but a directly constructed body or internal caller
    # bypasses that validation. Bare ``int()`` laundered those tokens
    # (``True``->1, ``5.9``->5, ``"١٢٣"``->123, ``"1_2"``->12) into a rule bound
    # to a DIFFERENT server. parse_id fails closed with 400 before any write.
    try:
        server_id = parse_id(body.server_id, "server_id")
    except (HardeningViolation, TypeError, ValueError):
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Invalid request data"}},
        )

    try:
        rule_type = RuleType(body.rule_type)
    except Exception:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Invalid rule type"}},
        )
    try:
        rule = automod.create_rule(
            user_id=int(admin_id),
            server_id=server_id,
            name=body.name,
            rule_type=rule_type,
            rule_config=body.config,
            actions=[a.model_dump() for a in body.actions],
            exempt_roles=body.exempt_roles,
            exempt_channels=body.exempt_channels,
            priority=body.priority or 0,
            check_all=bool(body.check_all),
            # Platform admins are authorized via admin.moderation.rules above,
            # not server.automod membership; skip the manager-layer guard.
            _skip_authz=True,
        )
        if body.enabled is False:
            automod.set_rule_enabled(int(admin_id), rule.id, False, _skip_authz=True)
            rule = automod.get_rule(rule.id)
        return _rule_to_response(rule)
    except Exception as e:
        logger.error(f"Create automod rule error: {e}", exc_info=True)
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Failed to create rule"}},
        )


@router.patch(
    "/automod/rules/{rule_id}",
    response_model=AutomodRuleResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {"model": ErrorResponse, "description": "Insufficient permissions"},
        404: {"model": ErrorResponse, "description": "Rule not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def update_automod_rule(
    rule_id: SnowflakeIDParam,
    body: AutomodRuleUpdateRequest,
    request: Request,
):
    """
    Update an existing AutoMod rule.
    """
    check_host_restriction(request)
    admin_id = require_admin_permission(request, "admin.moderation.rules")
    from src.core import automod

    try:
        update_kwargs: Dict[str, Any] = {}
        if body.name is not None:
            update_kwargs["name"] = body.name
        if body.config is not None:
            update_kwargs["rule_config"] = body.config
        if body.actions is not None:
            update_kwargs["actions"] = [a.model_dump() for a in body.actions]
        if body.exempt_roles is not None:
            update_kwargs["exempt_roles"] = body.exempt_roles
        if body.exempt_channels is not None:
            update_kwargs["exempt_channels"] = body.exempt_channels
        if body.priority is not None:
            update_kwargs["priority"] = body.priority
        if body.check_all is not None:
            update_kwargs["check_all"] = body.check_all
        if update_kwargs:
            automod.update_rule(
                user_id=int(admin_id),
                rule_id=rule_id,
                # Authorized via admin.moderation.rules above.
                _skip_authz=True,
                **update_kwargs,
            )
        if body.enabled is not None:
            automod.set_rule_enabled(
                int(admin_id), rule_id, body.enabled, _skip_authz=True
            )
        rule = automod.get_rule(rule_id)
        if not rule:
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": 404, "message": "Rule not found"}},
            )
        return _rule_to_response(rule)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Update automod rule error: {e}", exc_info=True)
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Failed to update rule"}},
        )


@router.delete(
    "/automod/rules/{rule_id}",
    response_model=SuccessResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {"model": ErrorResponse, "description": "Insufficient permissions"},
        404: {"model": ErrorResponse, "description": "Rule not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def delete_automod_rule(rule_id: SnowflakeIDParam, request: Request):
    """
    Permanently delete an AutoMod rule.
    """
    check_host_restriction(request)
    admin_id = require_admin_permission(request, "admin.moderation.rules")
    from src.core import automod

    try:
        if not automod.delete_rule(
            user_id=int(admin_id), rule_id=rule_id, _skip_authz=True
        ):
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": 404, "message": "Rule not found"}},
            )
        return SuccessResponse(success=True, message=None)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Delete automod rule error: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.get(
    "/automod/config",
    response_model=AutomodConfigResponse,
    responses={
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {"model": ErrorResponse, "description": "Insufficient permissions"},
    },
)
async def get_automod_config(request: Request):
    """
    Retrieve the global configuration settings for the AutoMod system.
    """
    check_host_restriction(request)
    require_admin_permission(request, "automod.read")
    automod_config = config.get("automod", {}) or {}
    return AutomodConfigResponse(
        enabled=automod_config.get("enabled", True),
        ai=automod_config.get("ai", {}),
    )


@router.put(
    "/automod/config",
    response_model=AutomodConfigResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {"model": ErrorResponse, "description": "Insufficient permissions"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def update_automod_config(body: AutomodConfigUpdateRequest, request: Request):
    """
    Update the global AutoMod configuration settings.
    """
    check_host_restriction(request)
    require_admin_permission(request, "admin.moderation.config")
    current = config.get("automod", {}) or {}
    if body.enabled is not None:
        current["enabled"] = body.enabled
    if body.ai is not None:
        merged = {**(current.get("ai", {}) or {}), **body.ai}
        current["ai"] = merged
    config.set("automod", current)
    try:
        from src.core import automod
        from fastapi.concurrency import run_in_threadpool

        await run_in_threadpool(automod.reload_config)
    except Exception:
        logger.warning("Failed to reload automod config after update", exc_info=True)
    return AutomodConfigResponse(
        enabled=current.get("enabled", True),
        ai=current.get("ai", {}),
    )
