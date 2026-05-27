"""
Admin user management routes.
"""

import time
from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel
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
    admin_id = get_admin_from_token(request)

    # Check permission
    from src.core.admin.permissions import check_admin_permission
    import src.api as api

    db = api.get_db()
    if db is None:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Database not available"}},
        )
    if not check_admin_permission(admin_id, "users.read", db):
        raise HTTPException(
            status_code=403,
            detail={"error": {"code": 403, "message": "Insufficient permissions"}},
        )

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


@router.get("/users/scheduled-deletions", response_model=ScheduledDeletionListResponse)
async def admin_list_scheduled_deletions(request: Request):
    """
    List all accounts currently in the 30-day grace period for deletion.
    """
    check_host_restriction(request)
    admin_id = get_admin_from_token(request)

    # Check permission
    from src.core.admin.permissions import check_admin_permission
    import src.api as api

    db = api.get_db()
    if db is None:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Database not available"}},
        )
    if not check_admin_permission(admin_id, "users.read", db):
        raise HTTPException(
            status_code=403,
            detail={"error": {"code": 403, "message": "Insufficient permissions"}},
        )

    import time

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
        if deletion_at is None:
            logger.warning(f"User {row['id']} has frozen status but no deletion_at")
            continue
        scheduled_at = deletion_at - (grace_days * 86400)
        days_left = max(0, (deletion_at - now) // 86400)

        logger.info(
            f"Scheduled deletion: user={row['username']}, deletion_at={deletion_at}, scheduled_at={scheduled_at}, days_left={days_left}"
        )

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


@router.get("/users/{user_id}", response_model=UserDetailsResponse)
async def get_user_details(request: Request, user_id: str):
    """
    Retrieve comprehensive information for a specific user.
    """
    check_host_restriction(request)
    admin_id = get_admin_from_token(request)

    # Check permission
    from src.core.admin.permissions import check_admin_permission
    import src.api as api

    db = api.get_db()
    if db is None:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Database not available"}},
        )
    if not check_admin_permission(admin_id, "users.read", db):
        raise HTTPException(
            status_code=403,
            detail={"error": {"code": 403, "message": "Insufficient permissions"}},
        )

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
    admin_id = get_admin_from_token(request)

    # Check permission
    from src.core.admin.permissions import check_admin_permission
    import src.api as api

    db = api.get_db()
    if db is None:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Database not available"}},
        )
    if not check_admin_permission(admin_id, "users.tier", db):
        raise HTTPException(
            status_code=403,
            detail={"error": {"code": 403, "message": "Insufficient permissions"}},
        )
    from src.core import admin

    try:
        uid = int(user_id)
        if not admin.update_user_tier(uid, update.tier):
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": 404, "message": "User not found"}},
            )
        return SuccessResponse(success=True, message="User tier updated successfully")
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

    # Check permission
    from src.core.admin.permissions import check_admin_permission
    import src.api as api

    db = api.get_db()
    if db is None:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Database not available"}},
        )
    if not check_admin_permission(admin_id, "users.badges", db):
        raise HTTPException(
            status_code=403,
            detail={"error": {"code": 403, "message": "Insufficient permissions"}},
        )

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

    # Check permission
    from src.core.admin.permissions import check_admin_permission
    import src.api as api

    db = api.get_db()
    if db is None:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Database not available"}},
        )
    if not check_admin_permission(admin_id, "users.badges", db):
        raise HTTPException(
            status_code=403,
            detail={"error": {"code": 403, "message": "Insufficient permissions"}},
        )

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
    admin_id = get_admin_from_token(request)

    # Check permission
    from src.core.admin.permissions import check_admin_permission
    import src.api as api

    db = api.get_db()
    if db is None:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Database not available"}},
        )
    if not check_admin_permission(admin_id, "users.notes", db):
        raise HTTPException(
            status_code=403,
            detail={"error": {"code": 403, "message": "Insufficient permissions"}},
        )

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

    # Check permission
    from src.core.admin.permissions import check_admin_permission
    import src.api as api

    db = api.get_db()
    if db is None:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Database not available"}},
        )
    if not check_admin_permission(admin_id, "users.notes", db):
        raise HTTPException(
            status_code=403,
            detail={"error": {"code": 403, "message": "Insufficient permissions"}},
        )

    from src.core import admin

    try:
        uid = int(user_id)
        admin.save_user_notes(uid, body.notes, admin_id)
        return SuccessResponse(success=True, message="User notes saved successfully")
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

    # Check permission
    from src.core.admin.permissions import check_admin_permission
    import src.api as api

    db = api.get_db()
    if db is None:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Database not available"}},
        )
    if not check_admin_permission(admin_id, "users.force_username_change", db):
        raise HTTPException(
            status_code=403,
            detail={"error": {"code": 403, "message": "Insufficient permissions"}},
        )
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
        return SuccessResponse(
            success=True, message="Username change forced successfully"
        )
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Invalid user ID"}},
        )


@router.post("/users/{user_id}/cancel-deletion", response_model=SuccessResponse)
async def admin_cancel_account_deletion(request: Request, user_id: str):
    """
    Cancel a scheduled account deletion and restore the account to 'active' status.
    """
    check_host_restriction(request)
    admin_id = get_admin_from_token(request)

    # Check permission
    from src.core.admin.permissions import check_admin_permission
    import src.api as api

    db = api.get_db()
    if db is None:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Database not available"}},
        )
    if not check_admin_permission(admin_id, "users.edit", db):
        raise HTTPException(
            status_code=403,
            detail={"error": {"code": 403, "message": "Insufficient permissions"}},
        )

    import src.api as api

    auth = api.get_auth()
    if not auth:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Auth module not available"}},
        )
    try:
        uid = int(user_id)
        auth.cancel_account_deletion(uid, admin_id=int(admin_id))
        return SuccessResponse(
            success=True, message="Account deletion cancelled successfully"
        )
    except Exception as e:
        logger.error(f"Admin failed to cancel deletion for {user_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": str(e)}},
        )


@router.post("/users/{user_id}/delay-deletion", response_model=SuccessResponse)
async def admin_delay_account_deletion(
    request: Request, user_id: str, deletion_at: int
):
    """
    Set a new deletion date for a scheduled account deletion.
    """
    check_host_restriction(request)
    admin_id = get_admin_from_token(request)

    # Check permission
    from src.core.admin.permissions import check_admin_permission
    import src.api as api

    db = api.get_db()
    if db is None:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Database not available"}},
        )
    if not check_admin_permission(admin_id, "users.edit", db):
        raise HTTPException(
            status_code=403,
            detail={"error": {"code": 403, "message": "Insufficient permissions"}},
        )

    import src.api as api

    auth = api.get_auth()
    if not auth:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Auth module not available"}},
        )
    try:
        uid = int(user_id)
        now = int(time.time())
        if deletion_at < now:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": {
                        "code": 400,
                        "message": "deletion_at must be in the future",
                    }
                },
            )
        auth.delay_account_deletion(uid, deletion_at, admin_id=int(admin_id))
        logger.info(f"Admin {admin_id} delayed deletion for user {uid}")
        return SuccessResponse(
            success=True, message="Account deletion delayed successfully"
        )
    except (ValueError, Exception) as e:
        # Check if it's a "not scheduled for deletion" error
        error_msg = str(e)
        if "not scheduled for deletion" in error_msg.lower():
            logger.warning(f"Admin failed to delay deletion for {user_id}: {e}")
            raise HTTPException(
                status_code=400,
                detail={
                    "error": {
                        "code": 400,
                        "message": "Account is not scheduled for deletion",
                    }
                },
            )

        if isinstance(e, ValueError):
            logger.warning(f"Admin failed to delay deletion for {user_id}: {e}")
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": str(e)}},
            )

        logger.error(f"Admin failed to delay deletion for {user_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": str(e)}},
        )


@router.post("/users/{user_id}/force-purge", response_model=SuccessResponse)
async def admin_force_purge_account(request: Request, user_id: str):
    """
    Immediately purge a user account, bypassing the grace period.
    This is irreversible and should only be used in extreme cases.
    """
    check_host_restriction(request)
    admin_id = get_admin_from_token(request)

    # Check permission
    from src.core.admin.permissions import check_admin_permission
    import src.api as api

    db = api.get_db()
    if db is None:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Database not available"}},
        )
    if not check_admin_permission(admin_id, "users.force_purge", db):
        raise HTTPException(
            status_code=403,
            detail={"error": {"code": 403, "message": "Insufficient permissions"}},
        )

    import src.api as api

    auth = api.get_auth()
    if not auth:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Auth module not available"}},
        )
    try:
        uid = int(user_id)
        auth.force_purge_account(uid, admin_id=int(admin_id))
        logger.warning(f"Admin {admin_id} force-purged user {uid}")
        return SuccessResponse(success=True, message="Account purged successfully")
    except Exception as e:
        logger.error(f"Admin failed to force purge {user_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": str(e)}},
        )


# Admin User Management Endpoints


class AdminUserCreate(BaseModel):
    username: str
    email: str
    password: str
    role: str = "admin"


class AdminUserUpdate(BaseModel):
    username: str | None = None
    email: str | None = None
    password: str | None = None
    role: str | None = None


class AdminUserResponse(BaseModel):
    id: str
    username: str
    email: str
    role: str
    is_active: bool
    created_at: int
    last_login_at: int | None = None


class AdminUserListResponse(BaseModel):
    users: list[AdminUserResponse]


@router.get("/admin-users", response_model=AdminUserListResponse)
async def list_admin_users(request: Request):
    """
    List all admin users.
    """
    check_host_restriction(request)
    admin_id = get_admin_from_token(request)

    # Check permission
    from src.core.admin.permissions import check_admin_permission
    import src.api as api

    db = api.get_db()
    if db is None:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Database not available"}},
        )
    if not check_admin_permission(admin_id, "admin.users", db):
        raise HTTPException(
            status_code=403,
            detail={"error": {"code": 403, "message": "Insufficient permissions"}},
        )

    try:
        # Query admin users from database
        # Note: email is stored encrypted (email_index + email_encrypted),
        # so we use email_index to confirm existence and show a placeholder.
        rows = db.fetch_all(  # type: ignore
            "SELECT id, username, email_index, created_at, last_login_at, account_locked FROM auth_users WHERE (permissions LIKE '%\"*\": true%' OR permissions LIKE '%\"admin.*\": true%') ORDER BY created_at DESC"
        )

        users = []
        for row in rows:
            users.append(
                AdminUserResponse(
                    id=str(row["id"]),
                    username=row["username"],
                    email="[Encrypted]",  # Email is stored encrypted, cannot be decrypted at API layer
                    role="admin",  # Default role for now
                    is_active=not row.get("account_locked", 0),
                    created_at=row["created_at"],
                    last_login_at=row["last_login_at"],
                )
            )

        return AdminUserListResponse(users=users)
    except Exception as e:
        logger.error(f"Failed to list admin users: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )


@router.get("/admin-users/{user_id}", response_model=AdminUserResponse)
async def get_admin_user(request: Request, user_id: str):
    """
    Get a specific admin user by ID.
    """
    check_host_restriction(request)
    admin_id = get_admin_from_token(request)

    # Check permission
    from src.core.admin.permissions import check_admin_permission
    import src.api as api

    db = api.get_db()
    if db is None:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Database not available"}},
        )
    if not check_admin_permission(admin_id, "admin.users", db):
        raise HTTPException(
            status_code=403,
            detail={"error": {"code": 403, "message": "Insufficient permissions"}},
        )

    try:
        uid = int(user_id)
        row = db.fetch_one(  # type: ignore
            "SELECT id, username, email_index, created_at, last_login_at, account_locked FROM auth_users WHERE id = ? AND (permissions LIKE '%\"*\": true%' OR permissions LIKE '%\"admin.*\": true%')",
            (uid,),
        )

        if not row:
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": 404, "message": "Admin user not found"}},
            )

        return AdminUserResponse(
            id=str(row["id"]),
            username=row["username"],
            email="[Encrypted]",  # Email is stored encrypted, cannot be decrypted at API layer
            role="admin",
            is_active=not row.get("account_locked", 0),
            created_at=row["created_at"],
            last_login_at=row["last_login_at"],
        )
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Invalid user ID"}},
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get admin user: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )


@router.post("/admin-users", response_model=SuccessResponse)
async def create_admin_user(request: Request, user_data: AdminUserCreate):
    """
    Create a new admin user.
    """
    check_host_restriction(request)
    admin_id = get_admin_from_token(request)

    # Check permission
    from src.core.admin.permissions import check_admin_permission
    import src.api as api

    db = api.get_db()
    if db is None:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Database not available"}},
        )
    if not check_admin_permission(admin_id, "admin.users", db):
        raise HTTPException(
            status_code=403,
            detail={"error": {"code": 403, "message": "Insufficient permissions"}},
        )

    try:
        import src.api as api

        auth = api.get_auth()
        assert auth is not None

        # Check if username already exists
        existing = db.fetch_one(  # type: ignore
            "SELECT id FROM auth_users WHERE username = ?", (user_data.username,)
        )
        if existing:
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Username already exists"}},
            )

        # Create the user first via the auth module
        user = auth.register(
            username=user_data.username,
            email=user_data.email,
            password=user_data.password,
        )

        # Grant admin permissions
        auth.grant_permission(user.id, "admin.*")
        auth.grant_permission(user.id, "*")

        logger.info(f"Admin user created: {user_data.username} by admin {admin_id}")
        return SuccessResponse(success=True, message="Admin user created successfully")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to create admin user: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )


@router.put("/admin-users/{user_id}", response_model=SuccessResponse)
async def update_admin_user(request: Request, user_id: str, user_data: AdminUserUpdate):
    """
    Update an existing admin user.
    """
    check_host_restriction(request)
    admin_id = get_admin_from_token(request)

    # Check permission
    from src.core.admin.permissions import check_admin_permission
    import src.api as api

    db = api.get_db()
    if db is None:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Database not available"}},
        )
    if not check_admin_permission(admin_id, "admin.users", db):
        raise HTTPException(
            status_code=403,
            detail={"error": {"code": 403, "message": "Insufficient permissions"}},
        )

    try:
        uid = int(user_id)

        # Check if user exists and is admin
        user = db.fetch_one(  # type: ignore
            "SELECT id, username, email_index FROM auth_users WHERE id = ? AND (permissions LIKE '%\"*\": true%' OR permissions LIKE '%\"admin.*\": true%')",
            (uid,),
        )
        if not user:
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": 404, "message": "Admin user not found"}},
            )

        # Build update query dynamically based on provided fields
        updates = []
        params = []

        if user_data.username:
            # Check if username already exists
            existing = db.fetch_one(  # type: ignore
                "SELECT id FROM auth_users WHERE username = ? AND id != ?",
                (user_data.username, uid),
            )
            if existing:
                raise HTTPException(
                    status_code=400,
                    detail={
                        "error": {"code": 400, "message": "Username already exists"}
                    },
                )
            updates.append("username = ?")
            params.append(user_data.username)

        if user_data.email:
            # Email updates must go through the auth module's update_user flow
            # which handles re-encryption and verification. Direct SQL updates
            # to the encrypted email columns are not supported from this endpoint.
            import src.api as api

            auth = api.get_auth()
            if auth:
                auth.update_user(uid, email=user_data.email)

        if user_data.password:
            from src.utils.encryption import hash_password

            password_hash = hash_password(user_data.password)
            updates.append("password_hash = ?")
            params.append(password_hash)

        if updates:
            params.append(uid)
            query = f"UPDATE auth_users SET {', '.join(updates)} WHERE id = ?"
            db.execute(query, tuple(params))  # type: ignore
            logger.info(f"Admin user updated: {user_id} by admin {admin_id}")

        return SuccessResponse(success=True, message="Admin user updated successfully")
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Invalid user ID"}},
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update admin user: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )


@router.delete("/admin-users/{user_id}", response_model=SuccessResponse)
async def delete_admin_user(request: Request, user_id: str):
    """
    Delete an admin user.
    """
    check_host_restriction(request)
    admin_id = get_admin_from_token(request)

    # Check permission
    from src.core.admin.permissions import check_admin_permission
    import src.api as api

    db = api.get_db()
    if db is None:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Database not available"}},
        )
    if not check_admin_permission(admin_id, "admin.users", db):
        raise HTTPException(
            status_code=403,
            detail={"error": {"code": 403, "message": "Insufficient permissions"}},
        )

    try:
        uid = int(user_id)

        # Prevent deleting yourself
        if str(uid) == str(admin_id):
            raise HTTPException(
                status_code=400,
                detail={
                    "error": {"code": 400, "message": "Cannot delete your own account"}
                },
            )

        # Check if user exists and is admin
        user = db.fetch_one(  # type: ignore
            "SELECT id FROM auth_users WHERE id = ? AND (permissions LIKE '%\"*\": true%' OR permissions LIKE '%\"admin.*\": true%')",
            (uid,),
        )
        if not user:
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": 404, "message": "Admin user not found"}},
            )

        # Delete user
        db.execute("DELETE FROM auth_users WHERE id = ?", (uid,))  # type: ignore
        logger.info(f"Admin user deleted: {user_id} by admin {admin_id}")

        return SuccessResponse(success=True, message="Admin user deleted successfully")
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Invalid user ID"}},
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete admin user: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )


@router.post("/admin-users/{user_id}/toggle-status", response_model=SuccessResponse)
async def toggle_admin_user_status(request: Request, user_id: str):
    """
    Toggle the active status of an admin user.
    """
    check_host_restriction(request)
    admin_id = get_admin_from_token(request)

    # Check permission
    from src.core.admin.permissions import check_admin_permission
    import src.api as api

    db = api.get_db()
    if db is None:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Database not available"}},
        )
    if not check_admin_permission(admin_id, "admin.users", db):
        raise HTTPException(
            status_code=403,
            detail={"error": {"code": 403, "message": "Insufficient permissions"}},
        )

    try:
        uid = int(user_id)

        # Prevent disabling yourself
        if str(uid) == str(admin_id):
            raise HTTPException(
                status_code=400,
                detail={
                    "error": {"code": 400, "message": "Cannot disable your own account"}
                },
            )

        # Check if user exists and is admin
        user = db.fetch_one(  # type: ignore
            "SELECT id, account_locked FROM auth_users WHERE id = ? AND (permissions LIKE '%\"*\": true%' OR permissions LIKE '%\"admin.*\": true%')",
            (uid,),
        )
        if not user:
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": 404, "message": "Admin user not found"}},
            )

        # Toggle account lock status
        new_status = 0 if user["account_locked"] == 1 else 1
        db.execute(  # type: ignore
            "UPDATE auth_users SET account_locked = ? WHERE id = ?", (new_status, uid)
        )
        logger.info(
            f"Admin user status toggled: {user_id} to {new_status} by admin {admin_id}"
        )

        return SuccessResponse(
            success=True, message="Admin user status updated successfully"
        )
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Invalid user ID"}},
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to toggle admin user status: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )
