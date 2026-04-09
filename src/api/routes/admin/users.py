"""
Admin user management routes.
"""

from fastapi import APIRouter, Request, HTTPException
from src.api.schemas.admin import (
    UserSearchListResponse,
    UserSearchResponse,
    UserDetailsResponse,
    UserTierUpdate,
    UserBadgeUpdateResponse,
    UserNotesResponse,
    UserNotesUpdate,
    ForceUsernameChangeRequest,
    ScheduledDeletionListResponse,
    ScheduledDeletionResponse,
)
from src.api.schemas.common import SuccessResponse
from .utils import check_host_restriction, get_admin_from_token
import utils.logger as logger

router = APIRouter()


@router.get("/users/search", response_model=UserSearchListResponse)
async def admin_user_search(request: Request, q: str, limit: int = 20, offset: int = 0):
    """
    Search for users based on a query string.

    Supports matching by username or email and provides paginated results.
    """
    check_host_restriction(request)
    get_admin_from_token(request)
    from src.core import admin

    try:
        users_data = admin.search_users(q, limit, offset)
        return UserSearchListResponse(
            users=[
                UserSearchResponse(
                    id=str(u.id),
                    username=u.username,
                    email=u.email,
                    tier=u.tier,
                    badges=u.badges,
                    created_at=u.created_at,
                    deletion_status=u.deletion_status,
                    deletion_at=u.deletion_at,
                )
                for u in users_data
            ]
        )
    except Exception as e:
        logger.error(f"User search error: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )


@router.get("/users/{user_id}", response_model=UserDetailsResponse)
async def get_user_details(request: Request, user_id: str):
    """
    Retrieve comprehensive information for a specific user.
    """
    check_host_restriction(request)
    get_admin_from_token(request)
    from src.core import admin

    try:
        uid = int(user_id)
        user = admin.get_user_details(uid)
        if not user:
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": 404, "message": "User not found"}},
            )
        return UserDetailsResponse(
            id=str(user.id),
            username=user.username,
            email=user.email,
            tier=user.tier,
            badges=user.badges,
            created_at=user.created_at,
            last_login=user.last_login,
            account_locked=user.account_locked,
            locked_until=user.locked_until,
            force_username_change=user.force_username_change,
            deletion_status=user.deletion_status,
            deletion_at=user.deletion_at,
        )
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Invalid user ID"}},
        )


@router.put("/users/{user_id}/tier", response_model=SuccessResponse)
async def update_user_tier(request: Request, user_id: str, update: UserTierUpdate):
    """
    Change the account tier (e.g., standard, premium) for a user.
    """
    check_host_restriction(request)
    get_admin_from_token(request)
    from src.core import admin

    try:
        uid = int(user_id)
        if not admin.update_user_tier(uid, update.tier):
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": 404, "message": "User not found"}},
            )
        return SuccessResponse(success=True)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Invalid user ID"}},
        )


@router.post("/users/{user_id}/badges/{badge}", response_model=UserBadgeUpdateResponse)
async def add_user_badge(request: Request, user_id: str, badge: str):
    """
    Assign a specific badge to a user's profile.
    """
    check_host_restriction(request)
    admin_id = get_admin_from_token(request)
    from src.core import admin

    try:
        uid = int(user_id)
        badges = admin.add_user_badge(uid, badge, admin_id)
        if badges is None:
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": 404, "message": "User not found"}},
            )
        return UserBadgeUpdateResponse(success=True, badges=badges)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Invalid user ID"}},
        )


@router.delete(
    "/users/{user_id}/badges/{badge}", response_model=UserBadgeUpdateResponse
)
async def remove_user_badge(request: Request, user_id: str, badge: str):
    """
    Remove a badge from a user's profile.
    """
    check_host_restriction(request)
    admin_id = get_admin_from_token(request)
    from src.core import admin

    try:
        uid = int(user_id)
        badges = admin.remove_user_badge(uid, badge, admin_id)
        if badges is None:
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": 404, "message": "User not found"}},
            )
        return UserBadgeUpdateResponse(success=True, badges=badges)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Invalid user ID"}},
        )


@router.get("/users/{user_id}/notes", response_model=UserNotesResponse)
async def get_user_notes(request: Request, user_id: str):
    """
    Retrieve internal administrator notes for a specific user.
    """
    check_host_restriction(request)
    get_admin_from_token(request)
    from src.core import admin

    try:
        uid = int(user_id)
        notes = admin.get_user_notes(uid)
        return UserNotesResponse(user_id=user_id, notes=notes)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Invalid user ID"}},
        )


@router.post("/users/{user_id}/notes", response_model=SuccessResponse)
async def update_user_notes(request: Request, user_id: str, body: UserNotesUpdate):
    """
    Update the internal administrator notes for a user.
    """
    check_host_restriction(request)
    admin_id = get_admin_from_token(request)
    from src.core import admin

    try:
        uid = int(user_id)
        admin.save_user_notes(uid, body.notes, admin_id)
        return SuccessResponse(success=True)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Invalid user ID"}},
        )


@router.post("/users/{user_id}/force-username-change", response_model=SuccessResponse)
async def admin_force_username_change(
    request: Request, user_id: str, body: ForceUsernameChangeRequest
):
    """
    Flag a user account to require a username change upon their next login.
    """
    check_host_restriction(request)
    admin_id = get_admin_from_token(request)
    from src.core import admin

    try:
        uid = int(user_id)
        if body.ban_current:
            user = admin.get_user_details(uid)
            if user:
                admin.add_banned_username(
                    user.username, body.reason or "Forced change", admin_id, False
                )
        admin.force_username_change(uid, True)
        return SuccessResponse(success=True)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Invalid user ID"}},
        )


@router.get("/users/scheduled-deletions", response_model=ScheduledDeletionListResponse)
async def admin_list_scheduled_deletions(request: Request):
    """
    List all accounts currently in the 30-day grace period for deletion.
    """
    check_host_restriction(request)
    get_admin_from_token(request)
    import src.api as api
    import time

    db = api.get_db()
    assert db is not None
    grace_days = 30  # Default grace period

    rows = db.fetch_all(  # type: ignore
        "SELECT id, username, deletion_at FROM auth_users WHERE deletion_status = 'frozen' ORDER BY deletion_at ASC"
    )

    # We also check the backup table and audit log in a real production environment
    # but for the API we show what's currently active in the DB.

    now = int(time.time())
    deletions = []
    for row in rows:
        deletion_at = row["deletion_at"]
        scheduled_at = deletion_at - (grace_days * 86400)
        days_left = max(0, (deletion_at - now) // 86400)

        deletions.append(
            ScheduledDeletionResponse(
                user_id=str(row["id"]),
                username=row["username"],
                scheduled_at=scheduled_at,
                deletion_at=deletion_at,
                days_left=days_left,
            )
        )

    return ScheduledDeletionListResponse(deletions=deletions)


@router.post("/users/{user_id}/cancel-deletion", response_model=SuccessResponse)
async def admin_cancel_account_deletion(request: Request, user_id: str):
    """
    Cancel a scheduled account deletion and restore the account to 'active' status.
    """
    check_host_restriction(request)
    admin_id = get_admin_from_token(request)
    import src.api as api

    auth = api.get_auth()
    assert auth is not None
    try:
        uid = int(user_id)
        auth.cancel_account_deletion(uid, admin_id=int(admin_id))
        return SuccessResponse(success=True)
    except Exception as e:
        logger.error(f"Admin failed to cancel deletion for {user_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/users/{user_id}/delay-deletion", response_model=SuccessResponse)
async def admin_delay_account_deletion(
    request: Request, user_id: str, additional_days: int = 7
):
    """
    Extend the deletion grace period for a scheduled account deletion.
    """
    check_host_restriction(request)
    admin_id = get_admin_from_token(request)
    import src.api as api

    auth = api.get_auth()
    assert auth is not None
    try:
        uid = int(user_id)
        if additional_days < 1 or additional_days > 365:
            raise HTTPException(
                status_code=400,
                detail="additional_days must be between 1 and 365",
            )
        auth.delay_account_deletion(uid, additional_days, admin_id=int(admin_id))
        return SuccessResponse(success=True)
    except Exception as e:
        logger.error(f"Admin failed to delay deletion for {user_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/users/{user_id}/force-purge", response_model=SuccessResponse)
async def admin_force_purge_account(request: Request, user_id: str):
    """
    Immediately purge a user account, bypassing the grace period.
    This is irreversible and should only be used in extreme cases.
    """
    check_host_restriction(request)
    admin_id = get_admin_from_token(request)
    import src.api as api

    auth = api.get_auth()
    assert auth is not None
    try:
        uid = int(user_id)
        auth.force_purge_account(uid, admin_id=int(admin_id))
        return SuccessResponse(success=True)
    except Exception as e:
        logger.error(f"Admin failed to force purge {user_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
