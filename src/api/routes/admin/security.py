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
    AccessTokenUpdateRequest,
    AccessTokenRotateRequest,
    AccessTokenScopeCreateRequest,
    AccessTokenScopeResponse,
    AccessTokenDetailResponse,
    AccessTokenUsageEventResponse,
    AccessTokenUsageIPResponse,
    AccessTokenUsagePathResponse,
)
from src.api.schemas.common import SuccessResponse
from .utils import check_host_restriction, get_admin_from_token

router = APIRouter()


def _serialize_access_token(token) -> AccessTokenResponse:
    return AccessTokenResponse(
        id=str(token.id),
        name=token.name,
        description=token.description,
        created_by=str(token.created_by) if token.created_by is not None else None,
        created_at=token.created_at,
        first_used_at=token.first_used_at,
        last_used_at=token.last_used_at,
        last_used_ip_address=token.last_used_ip_address,
        last_used_user_agent=token.last_used_user_agent,
        last_used_path=token.last_used_path,
        expires_at=token.expires_at,
        scope_mode=token.scope_mode,
        use_count_total=token.use_count_total,
        distinct_ip_count=token.distinct_ip_count,
        denied_count_total=token.denied_count_total,
        revoked=token.revoked,
        revoked_at=token.revoked_at,
        revoked_by=str(token.revoked_by) if token.revoked_by is not None else None,
    )


def _serialize_access_token_scope(scope) -> AccessTokenScopeResponse:
    return AccessTokenScopeResponse(
        id=str(scope["id"]),
        scope_type=scope["scope_type"],
        value=scope["value"],
        created_by=str(scope["created_by"]) if scope.get("created_by") is not None else None,
        created_at=scope["created_at"],
    )


@router.get("/security/blocked-ips", response_model=List[BlockedIPResponse])
async def get_blocked_ips(request: Request):
    """
    Retrieve a list of all currently blocked IP addresses.
    """
    check_host_restriction(request)
    get_admin_from_token(request)
    from src.core import auth

    return [BlockedIPResponse(**ip) for ip in auth.get_blocked_ips()]


@router.post("/security/block-ip", response_model=SuccessResponse)
async def block_ip(request: Request, body: IPBlockRequest):
    """
    Block a specific IP address from accessing the platform.
    """
    check_host_restriction(request)
    admin_id = get_admin_from_token(request)
    from src.core import auth

    auth.block_ip(body.ip_address, body.reason, admin_id, body.duration_hours)
    return SuccessResponse(success=True)


@router.get("/security/access-tokens", response_model=List[AccessTokenResponse])
async def list_access_tokens(request: Request, include_revoked: bool = True):
    """
    List all API access tokens created by administrators.
    """
    check_host_restriction(request)
    get_admin_from_token(request)
    from src.core import auth

    tokens = auth.list_api_access_tokens(include_revoked=include_revoked)
    return [_serialize_access_token(t) for t in tokens]


@router.post("/security/access-tokens", response_model=AccessTokenCreateResponse)
async def create_access_token(request: Request, body: AccessTokenCreateRequest):
    """
    Create a new permanent API access token for administrative use.
    """
    check_host_restriction(request)
    admin_id = get_admin_from_token(request)
    from src.core import auth

    try:
        token = auth.create_api_access_token(
            body.name,
            admin_id,
            body.token,
            description=body.description,
            expires_at=body.expires_at,
            scope_mode=body.scope_mode,
        )
    except ValueError as exc:
        status_code = 409 if "already exists" in str(exc).lower() else 400
        raise HTTPException(
            status_code=status_code,
            detail={"error": {"code": status_code, "message": str(exc)}},
        )
    return AccessTokenCreateResponse(
        token=token.token or "",
        access_token=_serialize_access_token(token),
    )


@router.get(
    "/security/access-tokens/{token_id}",
    response_model=AccessTokenDetailResponse,
)
async def get_access_token_detail(request: Request, token_id: int, recent_limit: int = 100):
    """Get detailed usage and policy information for a specific API access token."""
    check_host_restriction(request)
    get_admin_from_token(request)
    from src.core import auth

    try:
        detail = auth.get_api_access_token_usage(token_id, recent_limit=recent_limit)
    except Exception:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": 404, "message": "Access token not found"}},
        )

    return AccessTokenDetailResponse(
        access_token=_serialize_access_token(detail["token"]),
        scopes=[_serialize_access_token_scope(scope) for scope in detail["scopes"]],
        recent_events=[
            AccessTokenUsageEventResponse(
                id=str(event["id"]),
                used_at=event["used_at"],
                ip_address=event["ip_address"],
                method=event["method"],
                path=event["path"],
                user_agent=event["user_agent"],
                allowed=event["allowed"],
                scope_match=event["scope_match"],
                reject_reason=event["reject_reason"],
            )
            for event in detail["recent_events"]
        ],
        top_ips=[AccessTokenUsageIPResponse(**item) for item in detail["top_ips"]],
        top_paths=[AccessTokenUsagePathResponse(**item) for item in detail["top_paths"]],
        total_events=detail["total_events"],
        distinct_ip_count=detail["distinct_ip_count"],
        denied_count_total=detail["denied_count_total"],
    )


@router.patch(
    "/security/access-tokens/{token_id}",
    response_model=AccessTokenResponse,
)
async def update_access_token(
    request: Request,
    token_id: int,
    body: AccessTokenUpdateRequest,
):
    """Update metadata, expiry, or scope mode for a specific API access token."""
    check_host_restriction(request)
    admin_id = get_admin_from_token(request)
    from src.core import auth

    try:
        token = auth.update_api_access_token(
            token_id,
            admin_id,
            name=body.name,
            description=body.description,
            expires_at=body.expires_at,
            clear_expiry=body.clear_expiry,
            scope_mode=body.scope_mode,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": str(exc)}},
        )
    if not token:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": 404, "message": "Access token not found"}},
        )
    return _serialize_access_token(token)


@router.post(
    "/security/access-tokens/{token_id}/rotate",
    response_model=AccessTokenCreateResponse,
)
async def rotate_access_token(
    request: Request,
    token_id: int,
    body: AccessTokenRotateRequest,
):
    """Rotate an API access token, cloning policy and scopes into a replacement."""
    check_host_restriction(request)
    admin_id = get_admin_from_token(request)
    from src.core import auth

    try:
        token = auth.rotate_api_access_token(token_id, admin_id, body.token)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": str(exc)}},
        )
    if not token:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": 404, "message": "Access token not found"}},
        )
    return AccessTokenCreateResponse(token=token.token or "", access_token=_serialize_access_token(token))


@router.post(
    "/security/access-tokens/{token_id}/scopes",
    response_model=AccessTokenScopeResponse,
)
async def add_access_token_scope(
    request: Request,
    token_id: int,
    body: AccessTokenScopeCreateRequest,
):
    """Add an IP or CIDR scope rule to an API access token."""
    check_host_restriction(request)
    admin_id = get_admin_from_token(request)
    from src.core import auth

    try:
        scope = auth.add_api_access_token_scope(
            token_id,
            body.scope_type,
            body.value,
            admin_id,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": str(exc)}},
        )
    return _serialize_access_token_scope(scope)


@router.delete(
    "/security/access-tokens/{token_id}/scopes/{scope_id}",
    response_model=SuccessResponse,
)
async def remove_access_token_scope(request: Request, token_id: int, scope_id: int):
    """Remove an IP or CIDR scope rule from an API access token."""
    check_host_restriction(request)
    get_admin_from_token(request)
    from src.core import auth

    success = auth.remove_api_access_token_scope(token_id, scope_id)
    if not success:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": 404, "message": "Access token scope not found"}},
        )
    return SuccessResponse(success=True)


@router.post(
    "/security/access-tokens/{token_id}/revoke", response_model=SuccessResponse
)
async def revoke_access_token(request: Request, token_id: int):
    """
    Revoke a specific API access token.
    """
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
    """
    Remove an IP address from the blocklist.
    """
    check_host_restriction(request)
    get_admin_from_token(request)
    from src.core import auth

    auth.unblock_ip(ip_address)
    return SuccessResponse(success=True)


@router.get("/security/banned-usernames", response_model=List[BannedUsernameResponse])
async def get_banned_usernames(request: Request):
    """
    Retrieve the list of patterns used to ban specific usernames.
    """
    check_host_restriction(request)
    get_admin_from_token(request)
    from src.core import admin

    return [
        BannedUsernameResponse(**p.__dict__) if hasattr(p, "__dict__") else p
        for p in admin.get_banned_usernames()
    ]


@router.post("/security/banned-usernames", response_model=SuccessResponse)
async def add_banned_username(request: Request, body: BannedUsernameCreate):
    """
    Add a new pattern to the list of banned usernames.
    """
    check_host_restriction(request)
    admin_id = get_admin_from_token(request)
    from src.core import admin

    admin.add_banned_username(body.pattern, body.reason, admin_id, body.is_regex)  # type: ignore
    return SuccessResponse(success=True)


@router.delete(
    "/security/banned-usernames/{pattern_id}", response_model=SuccessResponse
)
async def remove_banned_username(request: Request, pattern_id: int):
    """
    Remove a pattern from the list of banned usernames.
    """
    check_host_restriction(request)
    get_admin_from_token(request)
    from src.core import admin

    admin.remove_banned_username(pattern_id)
    return SuccessResponse(success=True)


@router.post("/security/force-logout", response_model=SuccessResponse)
async def force_logout(request: Request, body: ForceLogoutRequest):
    """
    Immediately invalidate all active sessions for a specific user.
    """
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
    """
    Lock a user account, preventing all access for a specified duration.
    """
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
    """
    Unlock a previously locked user account.
    """
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
    """
    Invalidate all active sessions for every user on the platform.
    """
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
