"""
Admin security routes.
"""

from fastapi import APIRouter, Request, HTTPException, status
from typing import List
from src.api.schemas.admin import (
    BlockedIPResponse, IPBlockRequest, BannedUsernameResponse, BannedUsernameCreate,
    ForceLogoutRequest, UserLockRequest
)
from src.api.schemas.common import SuccessResponse
from .utils import check_host_restriction, get_admin_from_token
import utils.logger as logger

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
    return [BannedUsernameResponse(**p.__dict__) if hasattr(p, '__dict__') else p for p in admin.get_banned_usernames()]

@router.post("/security/banned-usernames", response_model=SuccessResponse)
async def add_banned_username(request: Request, body: BannedUsernameCreate):
    check_host_restriction(request)
    admin_id = get_admin_from_token(request)
    from src.core import admin
    admin.add_banned_username(body.pattern, body.reason, admin_id, body.is_regex)
    return SuccessResponse(success=True)

@router.delete("/security/banned-usernames/{pattern_id}", response_model=SuccessResponse)
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
                event = Event(event_type=EventType.SECURITY_LOGOUT, data={"user_id": str(uid), "message": "Security logout"})
                await get_dispatcher().dispatch_event(event, [uid])
        except Exception: pass
        return SuccessResponse(success=True)
    except ValueError:
        raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": "Invalid user ID"}})

@router.post("/security/lock-user", response_model=SuccessResponse)
async def admin_lock_user(request: Request, body: UserLockRequest):
    check_host_restriction(request)
    admin_id = get_admin_from_token(request)
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
                event = Event(event_type=EventType.SECURITY_LOGOUT, data={"user_id": str(uid), "message": "Account suspended"})
                await get_dispatcher().dispatch_event(event, [uid])
        except Exception: pass
        return SuccessResponse(success=True)
    except ValueError:
        raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": "Invalid user ID"}})

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
        raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": "Invalid user ID"}})

@router.post("/security/logout-all", response_model=SuccessResponse)
async def logout_all_users(request: Request):
    check_host_restriction(request)
    get_admin_from_token(request)
    from src.core import auth
    auth.logout_all_users()
    try:
        from src.api.websocket import get_dispatcher, is_setup as ws_is_setup
        if ws_is_setup():
            await get_dispatcher().close_all_connections(close_code=4004, reason="Security reset")
    except Exception: pass
    return SuccessResponse(success=True)
