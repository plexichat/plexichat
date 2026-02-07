"""
Admin user management routes.
"""

from fastapi import APIRouter, Request, HTTPException
from src.api.schemas.admin import (
    UserSearchListResponse, UserSearchResponse, UserDetailsResponse,
    UserTierUpdate, UserBadgeUpdateResponse, UserNotesResponse, UserNotesUpdate,
    ForceUsernameChangeRequest
)
from src.api.schemas.common import SuccessResponse
from .utils import check_host_restriction, get_admin_from_token
import utils.logger as logger

router = APIRouter()

@router.get("/users/search", response_model=UserSearchListResponse)
async def admin_user_search(request: Request, q: str, limit: int = 20, offset: int = 0):
    check_host_restriction(request)
    get_admin_from_token(request)
    from src.core import admin
    try:
        users_data = admin.search_users(q, limit, offset)
        return UserSearchListResponse(users=[
            UserSearchResponse(
                id=str(u.id), username=u.username, email=u.email, tier=u.tier,
                badges=u.badges, created_at=u.created_at
            ) for u in users_data
        ])
    except Exception as e:
        logger.error(f"User search error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": str(e)}})

@router.get("/users/{user_id}", response_model=UserDetailsResponse)
async def get_user_details(request: Request, user_id: str):
    check_host_restriction(request)
    get_admin_from_token(request)
    from src.core import admin
    try:
        uid = int(user_id)
        user = admin.get_user_details(uid)
        if not user:
            raise HTTPException(status_code=404, detail={"error": {"code": 404, "message": "User not found"}})
        return UserDetailsResponse(
            id=str(user.id), username=user.username, email=user.email, tier=user.tier,
            badges=user.badges, created_at=user.created_at, last_login=user.last_login,
            account_locked=user.account_locked, locked_until=user.locked_until,
            force_username_change=user.force_username_change
        )
    except ValueError:
        raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": "Invalid user ID"}})

@router.put("/users/{user_id}/tier", response_model=SuccessResponse)
async def update_user_tier(request: Request, user_id: str, update: UserTierUpdate):
    check_host_restriction(request)
    get_admin_from_token(request)
    from src.core import admin
    try:
        uid = int(user_id)
        if not admin.update_user_tier(uid, update.tier):
            raise HTTPException(status_code=404, detail={"error": {"code": 404, "message": "User not found"}})
        return SuccessResponse(success=True)
    except ValueError:
        raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": "Invalid user ID"}})

@router.post("/users/{user_id}/badges/{badge}", response_model=UserBadgeUpdateResponse)
async def add_user_badge(request: Request, user_id: str, badge: str):
    check_host_restriction(request)
    admin_id = get_admin_from_token(request)
    from src.core import admin
    try:
        uid = int(user_id)
        badges = admin.add_user_badge(uid, badge, admin_id)
        if badges is None:
            raise HTTPException(status_code=404, detail={"error": {"code": 404, "message": "User not found"}})
        return UserBadgeUpdateResponse(success=True, badges=badges)
    except ValueError:
        raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": "Invalid user ID"}})

@router.delete("/users/{user_id}/badges/{badge}", response_model=UserBadgeUpdateResponse)
async def remove_user_badge(request: Request, user_id: str, badge: str):
    check_host_restriction(request)
    admin_id = get_admin_from_token(request)
    from src.core import admin
    try:
        uid = int(user_id)
        badges = admin.remove_user_badge(uid, badge, admin_id)
        if badges is None:
            raise HTTPException(status_code=404, detail={"error": {"code": 404, "message": "User not found"}})
        return UserBadgeUpdateResponse(success=True, badges=badges)
    except ValueError:
        raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": "Invalid user ID"}})

@router.get("/users/{user_id}/notes", response_model=UserNotesResponse)
async def get_user_notes(request: Request, user_id: str):
    check_host_restriction(request)
    get_admin_from_token(request)
    from src.core import admin
    try:
        uid = int(user_id)
        notes = admin.get_user_notes(uid)
        return UserNotesResponse(user_id=user_id, notes=notes)
    except ValueError:
        raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": "Invalid user ID"}})

@router.post("/users/{user_id}/notes", response_model=SuccessResponse)
async def update_user_notes(request: Request, user_id: str, body: UserNotesUpdate):
    check_host_restriction(request)
    admin_id = get_admin_from_token(request)
    from src.core import admin
    try:
        uid = int(user_id)
        admin.save_user_notes(uid, body.notes, admin_id)
        return SuccessResponse(success=True)
    except ValueError:
        raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": "Invalid user ID"}})

@router.post("/users/{user_id}/force-username-change", response_model=SuccessResponse)
async def admin_force_username_change(request: Request, user_id: str, body: ForceUsernameChangeRequest):
    check_host_restriction(request)
    admin_id = get_admin_from_token(request)
    from src.core import admin
    try:
        uid = int(user_id)
        if body.ban_current:
            user = admin.get_user_details(uid)
            if user:
                admin.add_banned_username(user.username, body.reason or "Forced change", admin_id, False)
        admin.force_username_change(uid, True)
        return SuccessResponse(success=True)
    except ValueError:
        raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": "Invalid user ID"}})
