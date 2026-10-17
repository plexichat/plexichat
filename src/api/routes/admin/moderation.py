"""
Admin moderation and content review routes.
"""

from fastapi import APIRouter, Query, Request, HTTPException
from typing import List, Optional, Dict, Any
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
from src.api.schemas.common import (
    ErrorResponse,
    SnowflakeIDParam,
    SuccessResponse,
)
from .utils import (
    check_host_restriction,
    get_admin_from_token,
    require_admin_permission,
)
import src.api as api
import utils.logger as logger
import utils.config as config

router = APIRouter()


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
    row = db.fetch_one("SELECT id FROM admin_users WHERE id = ?", (target_user_id,))
    if not row:
        return
    from src.core.admin.permissions import can_manage_admin

    if not can_manage_admin(db, int(admin_id), int(target_user_id)):
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
    status_filter: Optional[str] = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """
    Retrieve a list of content hash reports for review.

    Allows filtering by status and supports pagination.
    """
    check_host_restriction(request)
    get_admin_from_token(request)
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
    get_admin_from_token(request)
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
    status_filter: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
):
    """Retrieve message reports for admin review."""
    check_host_restriction(request)
    get_admin_from_token(request)
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
    get_admin_from_token(request)
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
    status_filter: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
):
    """List enhanced (``reports`` table) reports for moderator review.

    The enhanced submission pipeline persists to the ``reports`` table, which
    is separate from ``message_reports``/``user_reports``. Exposing it here
    keeps those reports visible in the moderator queue instead of orphaned.
    """
    check_host_restriction(request)
    get_admin_from_token(request)
    db = api.get_db()
    if db is None:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Database unavailable"}},
        )
    limit = max(1, min(int(limit), 100))
    offset = max(0, int(offset))
    clauses = []
    params: List[Any] = []
    if status_filter:
        clauses.append("status = ?")
        params.append(status_filter)
    where = (" AND ".join(clauses)) if clauses else "1=1"
    rows = db.fetch_all(
        f"SELECT * FROM reports WHERE {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
        tuple(params + [limit, offset]),
    )
    total_row = db.fetch_one(
        f"SELECT COUNT(*) AS count FROM reports WHERE {where}", tuple(params)
    )
    total = int(total_row["count"]) if total_row else 0
    return {
        "items": [dict(row) for row in rows],
        "total": total,
        "has_more": offset + len(rows) < total,
    }


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
    get_admin_from_token(request)
    db = api.get_db()
    if db is None:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Database unavailable"}},
        )
    counts: Dict[str, int] = {}
    rows = db.fetch_all("SELECT status, COUNT(*) as count FROM reports GROUP BY status")
    total = 0
    for row in rows:
        status = row["status"]
        count = int(row["count"])
        counts[status] = count
        total += count
    counts["total"] = total
    return counts


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
    status_filter: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
):
    """Retrieve user behavior reports for admin review."""
    check_host_restriction(request)
    get_admin_from_token(request)
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
    get_admin_from_token(request)
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
    offset: int = Query(0, ge=0),
):
    """
    List all currently blocked content hashes.
    """
    check_host_restriction(request)
    get_admin_from_token(request)
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
    offset: int = Query(0, ge=0),
):
    """
    Retrieve a list of all currently blocked or banned users.
    """
    check_host_restriction(request)
    get_admin_from_token(request)
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

    target_id = int(block_request.user_id)
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
async def unblock_user(user_id: str, request: Request):
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
                action_type=a.action_type.value
                if hasattr(a.action_type, "value")
                else str(a.action_type),
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
async def get_automod_rules(request: Request, server_id: str):
    """
    Retrieve the list of AutoMod rules configured for a specific server.
    """
    check_host_restriction(request)
    get_admin_from_token(request)
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
            server_id=int(body.server_id),
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
    get_admin_from_token(request)
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
