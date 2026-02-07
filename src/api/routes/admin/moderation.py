"""
Admin moderation and content review routes.
"""

from fastapi import APIRouter, Request, HTTPException
from typing import List, Optional
from src.api.schemas.admin import (
    HashReportResponse, HashReportCountsResponse, HashReportReviewRequest, HashReportReviewResponse,
    BlockedHashResponse, ManualBlockHashRequest, BlockHashResponse,
    BlockedUserResponse, BlockUserRequest, BlockUserResponse
)
from src.api.schemas.common import SuccessResponse
from .utils import check_host_restriction, get_admin_from_token
import utils.logger as logger

router = APIRouter()

@router.get("/hash-reports", response_model=List[HashReportResponse])
async def get_hash_reports(request: Request, status_filter: Optional[str] = None, limit: int = 50, offset: int = 0):
    check_host_restriction(request)
    get_admin_from_token(request)
    from src.core import admin
    try:
        reports = admin.get_hash_reports(status_filter, limit, offset)
        return [
            HashReportResponse(
                id=str(r.id), hash_value=r.hash_value, phash_value=r.phash_value,
                reporter_id=str(r.reporter_id), reporter_username=r.reporter_username,
                reason=r.reason, details=r.details, status=r.status, reported_at=r.reported_at,
                reviewed_at=r.reviewed_at, reviewed_by=str(r.reviewed_by) if r.reviewed_by else None,
                admin_notes=r.admin_notes, uploader_id=str(r.uploader_id) if r.uploader_id else None,
                message_id=str(r.message_id) if r.message_id else None,
                attachment_url=r.attachment_url, block_uploader=r.block_uploader
            ) for r in reports
        ]
    except Exception as e:
        logger.error(f"Hash reports error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": str(e)}})

@router.get("/hash-reports/counts", response_model=HashReportCountsResponse)
async def get_hash_report_counts(request: Request):
    check_host_restriction(request)
    get_admin_from_token(request)
    from src.core import admin
    return HashReportCountsResponse(**admin.get_hash_report_counts())

@router.post("/hash-reports/{report_id}/review", response_model=HashReportReviewResponse)
async def review_hash_report(report_id: int, review: HashReportReviewRequest, request: Request):
    check_host_restriction(request)
    admin_id = get_admin_from_token(request)
    from src.core import admin
    if not admin.review_hash_report(report_id, admin_id, review.action, review.notes):
        raise HTTPException(status_code=404, detail={"error": {"code": 404, "message": "Report not found"}})
    return HashReportReviewResponse(success=True, action=review.action)

@router.get("/blocked-hashes", response_model=List[BlockedHashResponse])
async def get_blocked_hashes(request: Request, limit: int = 100, offset: int = 0):
    check_host_restriction(request)
    get_admin_from_token(request)
    from src.core import admin
    return [BlockedHashResponse(
        hash_value=h.hash_value, reason=h.reason, blocked_at=h.blocked_at,
        blocked_by=h.blocked_by, auto_blocked=h.auto_blocked,
        hash_type=h.hash_type, phash_threshold=h.phash_threshold
    ) for h in admin.get_blocked_hashes(limit, offset)]

@router.post("/blocked-hashes", response_model=BlockHashResponse)
async def block_hash_manually(block_request: ManualBlockHashRequest, request: Request):
    check_host_restriction(request)
    admin_id = get_admin_from_token(request)
    from src.core import admin
    hash_type = "phash" if len(block_request.hash_value) <= 32 else "sha256"
    phash_threshold = 10 if hash_type == "phash" else 0
    if not admin.block_hash(block_request.hash_value, block_request.reason, admin_id, hash_type, phash_threshold):
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Failed to block hash"}})
    return BlockHashResponse(success=True, hash_value=block_request.hash_value, hash_type=hash_type)

@router.delete("/blocked-hashes/{hash_value}", response_model=SuccessResponse)
async def unblock_hash(hash_value: str, request: Request):
    check_host_restriction(request)
    get_admin_from_token(request)
    from src.core import admin
    if not admin.unblock_hash(hash_value):
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Failed to unblock hash"}})
    return SuccessResponse(success=True)

@router.get("/blocked-users", response_model=List[BlockedUserResponse])
async def get_blocked_users(request: Request, limit: int = 100, offset: int = 0):
    check_host_restriction(request)
    get_admin_from_token(request)
    from src.core import admin
    return [BlockedUserResponse(
        user_id=u.user_id, username=u.username, reason=u.reason,
        blocked_at=u.blocked_at, blocked_by=u.blocked_by, expires_at=u.expires_at
    ) for u in admin.get_blocked_users(limit, offset)]

@router.post("/blocked-users", response_model=BlockUserResponse)
async def block_user(block_request: BlockUserRequest, request: Request):
    check_host_restriction(request)
    admin_id = get_admin_from_token(request)
    from src.core import admin
    if not admin.block_user(block_request.user_id, block_request.reason, admin_id, block_request.duration_hours):
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Failed to block user"}})
    return BlockUserResponse(success=True, user_id=block_request.user_id)

@router.delete("/blocked-users/{user_id}", response_model=SuccessResponse)
async def unblock_user(user_id: int, request: Request):
    check_host_restriction(request)
    get_admin_from_token(request)
    from src.core import admin
    if not admin.unblock_user(user_id):
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Failed to unblock user"}})
    return SuccessResponse(success=True)
