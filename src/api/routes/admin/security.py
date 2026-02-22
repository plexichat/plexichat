"""
Admin security routes.
"""

from fastapi import APIRouter, Request, HTTPException
from typing import List
from src.api.schemas.admin import (
    BlockedIPResponse,
    IPBlockRequest,
    BannedUsernameResponse,
    BannedUsernameCreate,
    ForceLogoutRequest,
    UserLockRequest,
    AccessTokenCreateRequest,
    AccessTokenCreateResponse,
    AccessTokenResponse,
)
from src.api.schemas.common import SuccessResponse
from .utils import check_host_restriction, get_admin_from_token

router = APIRouter()


@router.get("/security/blocked-ips", response_model=List[BlockedIPResponse])
async def get_blocked_ips(request: Request):
    check_host_restriction(request)
    get_admin_from_token(request)
    from src.core import auth

    return [BlockedIPResponse(**ip) for ip in auth.get_blocked_ips()]


@router.post("/security/block-ip", response_model=SuccessResponse)
async def block_ip(request: Request, body: IPBlockRequest):
    check_host_restriction(request)
    admin_id = get_admin_from_token(request)
    from src.core import auth

    auth.block_ip(body.ip_address, body.reason, admin_id, body.duration_hours)
    return SuccessResponse(success=True)


@router.get("/security/access-tokens", response_model=List[AccessTokenResponse])
async def list_access_tokens(request: Request, include_revoked: bool = True):
    check_host_restriction(request)
    get_admin_from_token(request)
    from src.core import auth

    tokens = auth.list_api_access_tokens(include_revoked=include_revoked)
    return [
        AccessTokenResponse(
            id=str(t.id),
            name=t.name,
            created_by=str(t.created_by) if t.created_by is not None else None,
            created_at=t.created_at,
            last_used_at=t.last_used_at,
            revoked=t.revoked,
            revoked_at=t.revoked_at,
            revoked_by=str(t.revoked_by) if t.revoked_by is not None else None,
        )
        for t in tokens
    ]


@router.post("/security/access-tokens", response_model=AccessTokenCreateResponse)
async def create_access_token(request: Request, body: AccessTokenCreateRequest):
    check_host_restriction(request)
    admin_id = get_admin_from_token(request)
    from src.core import auth

    try:
        token = auth.create_api_access_token(body.name, admin_id, body.token)
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail={"error": {"code": 409, "message": str(exc)}},
        )
    return AccessTokenCreateResponse(
        token=token.token or "",
        access_token=AccessTokenResponse(
            id=str(token.id),
            name=token.name,
            created_by=str(token.created_by) if token.created_by is not None else None,
            created_at=token.created_at,
            last_used_at=token.last_used_at,
            revoked=token.revoked,
            revoked_at=token.revoked_at,
            revoked_by=str(token.revoked_by) if token.revoked_by is not None else None,
        ),
    )


@router.post(
    "/security/access-tokens/{token_id}/revoke", response_model=SuccessResponse
)
async def revoke_access_token(request: Request, token_id: int):
    check_host_restriction(request)
    admin_id = get_admin_from_token(request)
    from src.core import auth

    success = auth.revoke_api_access_token(token_id, admin_id)
    if not success:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": 404, "message": "Access token not found"}},
        )
    return SuccessResponse(success=True)


@router.delete("/security/unblock-ip/{ip_address:path}", response_model=SuccessResponse)
async def unblock_ip(request: Request, ip_address: str):
    check_host_restriction(request)
    get_admin_from_token(request)
    from src.core import auth

    auth.unblock_ip(ip_address)
    return SuccessResponse(success=True)


@router.get("/security/banned-usernames", response_model=List[BannedUsernameResponse])
async def get_banned_usernames(request: Request):
    check_host_restriction(request)
    get_admin_from_token(request)
    from src.core import admin

    return [
        BannedUsernameResponse(**p.__dict__) if hasattr(p, "__dict__") else p
        for p in admin.get_banned_usernames()
    ]


@router.post("/security/banned-usernames", response_model=SuccessResponse)
async def add_banned_username(request: Request, body: BannedUsernameCreate):
    check_host_restriction(request)
    admin_id = get_admin_from_token(request)
    from src.core import admin

    admin.add_banned_username(body.pattern, body.reason, admin_id, body.is_regex)  # type: ignore
    return SuccessResponse(success=True)


@router.delete(
    "/security/banned-usernames/{pattern_id}", response_model=SuccessResponse
)
async def remove_banned_username(request: Request, pattern_id: int):
    check_host_restriction(request)
    get_admin_from_token(request)
    from src.core import admin

    admin.remove_banned_username(pattern_id)
    return SuccessResponse(success=True)


@router.post("/security/force-logout", response_model=SuccessResponse)
async def force_logout(request: Request, body: ForceLogoutRequest):
    check_host_restriction(request)
    get_admin_from_token(request)
    try:
        uid = int(body.user_id)
        from src.core import auth

        auth.logout_all(uid)
        # Broadcast via WebSocket
        try:
            from src.api.websocket import get_dispatcher, is_setup as ws_is_setup
            from src.core.events.models import Event
            from src.core.events.types import EventType

            if ws_is_setup():
                event = Event(
                    event_type=EventType.SECURITY_LOGOUT,
                    data={"user_id": str(uid), "message": "Security logout"},
                )
                await get_dispatcher().dispatch_event(event, [uid])
        except Exception:
            pass
        return SuccessResponse(success=True)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Invalid user ID"}},
        )


@router.post("/security/lock-user", response_model=SuccessResponse)
async def admin_lock_user(request: Request, body: UserLockRequest):
    check_host_restriction(request)
    get_admin_from_token(request)
    try:
        uid = int(body.user_id)
        from src.core import admin

        admin.lock_user(uid, body.duration_seconds)
        # Broadcast logout
        try:
            from src.api.websocket import get_dispatcher, is_setup as ws_is_setup
            from src.core.events.models import Event
            from src.core.events.types import EventType

            if ws_is_setup():
                event = Event(
                    event_type=EventType.SECURITY_LOGOUT,
                    data={"user_id": str(uid), "message": "Account suspended"},
                )
                await get_dispatcher().dispatch_event(event, [uid])
        except Exception:
            pass
        return SuccessResponse(success=True)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Invalid user ID"}},
        )


@router.post("/security/unlock-user", response_model=SuccessResponse)
async def admin_unlock_user(request: Request, body: ForceLogoutRequest):
    check_host_restriction(request)
    get_admin_from_token(request)
    try:
        uid = int(body.user_id)
        from src.core import admin

        admin.unlock_user(uid)
        return SuccessResponse(success=True)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Invalid user ID"}},
        )


@router.post("/security/logout-all", response_model=SuccessResponse)
async def logout_all_users(request: Request):
    check_host_restriction(request)
    get_admin_from_token(request)
    from src.core import auth

    auth.logout_all_users()
    try:
        from src.api.websocket import get_dispatcher, is_setup as ws_is_setup

        if ws_is_setup():
            await get_dispatcher().close_all_connections(
                close_code=4004, reason="Security reset"
            )
    except Exception:
        pass
    return SuccessResponse(success=True)
