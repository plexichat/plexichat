"""
Admin security routes.
"""

from fastapi import APIRouter, Request, HTTPException, Query
from typing import List
import secrets

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
from src.api.schemas.common import ErrorResponse, SnowflakeIDParam, SuccessResponse
from .utils import (
    check_host_restriction,
    require_admin_permission,
)
import utils.logger as logger

router = APIRouter()

# These three lists sit on attacker-growable tables: any holder of the matching
# security scope can create rows without a server-side cap. Serving the whole
# table in one request let a single admin turn a bloated table into an
# unbounded allocation (and, for blocked IPs, an unbounded per-row decrypt
# pass) that stalls a worker. Bound both the page size and how far it may
# paginate -- the same contract the other admin listing routes already use.
_MAX_SECURITY_LIST_LIMIT = 500
_MAX_SECURITY_LIST_OFFSET = 1_000_000


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
        created_by=str(scope["created_by"])
        if scope.get("created_by") is not None
        else None,
        created_at=scope["created_at"],
    )


@router.get(
    "/security/blocked-ips",
    response_model=List[BlockedIPResponse],
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_blocked_ips(
    request: Request,
    limit: int = Query(200, ge=1, le=_MAX_SECURITY_LIST_LIMIT),
    offset: int = Query(0, ge=0, le=_MAX_SECURITY_LIST_OFFSET),
):
    """
    Retrieve a bounded page of currently blocked IP addresses.
    """
    check_host_restriction(request)
    require_admin_permission(request, "admin.security.ipblock")
    from src.core import auth

    # blocked_by is the blocking admin's snowflake id; stringify it so the
    # response keeps the project-wide "ids are strings in JSON" contract.
    return [
        BlockedIPResponse(
            **{
                **ip,
                "blocked_by": (
                    str(ip["blocked_by"]) if ip.get("blocked_by") is not None else None
                ),
            }
        )
        for ip in auth.get_blocked_ips(limit=limit, offset=offset)
    ]


@router.post(
    "/security/block-ip",
    response_model=SuccessResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def block_ip(request: Request, body: IPBlockRequest):
    """
    Block a specific IP address from accessing the platform.
    """
    check_host_restriction(request)
    admin_id = require_admin_permission(request, "admin.security.ipblock")
    from src.core import auth

    auth.block_ip(body.ip_address, body.reason, admin_id, body.duration_hours)
    return SuccessResponse(success=True, message=None)


@router.get(
    "/security/access-tokens",
    response_model=List[AccessTokenResponse],
    responses={
        400: {"model": ErrorResponse, "description": "Invalid query parameters"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def list_access_tokens(
    request: Request,
    include_revoked: bool = True,
    limit: int = Query(200, ge=1, le=_MAX_SECURITY_LIST_LIMIT),
    offset: int = Query(0, ge=0, le=_MAX_SECURITY_LIST_OFFSET),
):
    """
    List a bounded page of API access tokens created by administrators.
    """
    check_host_restriction(request)
    require_admin_permission(request, "admin.security.tokens")
    from src.core import auth

    tokens = auth.list_api_access_tokens(
        include_revoked=include_revoked, limit=limit, offset=offset
    )
    return [_serialize_access_token(t) for t in tokens]


@router.post(
    "/security/access-tokens",
    response_model=AccessTokenCreateResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        409: {"model": ErrorResponse, "description": "Token name already exists"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def create_access_token(request: Request, body: AccessTokenCreateRequest):
    """
    Create a new permanent API access token for administrative use.

    The token value is always generated server-side; caller-supplied token
    values are rejected to prevent weak or pre-known credentials.
    """
    check_host_restriction(request)
    admin_id = require_admin_permission(request, "admin.security.tokens")
    if body.token:
        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "code": 400,
                    "message": "Token values are generated server-side and cannot be supplied by clients",
                }
            },
        )
    from src.core import auth

    try:
        token = auth.create_api_access_token(
            body.name,
            admin_id,
            secrets.token_urlsafe(32),
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
    responses={
        400: {"model": ErrorResponse, "description": "Invalid query parameters"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        404: {"model": ErrorResponse, "description": "Access token not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_access_token_detail(
    request: Request,
    token_id: SnowflakeIDParam,
    recent_limit: int = Query(100, ge=1, le=1000),
):
    """Get detailed usage and policy information for a specific API access token."""
    check_host_restriction(request)
    require_admin_permission(request, "admin.security.tokens")
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
        top_paths=[
            AccessTokenUsagePathResponse(**item) for item in detail["top_paths"]
        ],
        total_events=detail["total_events"],
        distinct_ip_count=detail["distinct_ip_count"],
        denied_count_total=detail["denied_count_total"],
    )


@router.patch(
    "/security/access-tokens/{token_id}",
    response_model=AccessTokenResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        404: {"model": ErrorResponse, "description": "Access token not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def update_access_token(
    request: Request,
    token_id: SnowflakeIDParam,
    body: AccessTokenUpdateRequest,
):
    """Update metadata, expiry, or scope mode for a specific API access token."""
    check_host_restriction(request)
    admin_id = require_admin_permission(request, "admin.security.tokens")
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
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        404: {"model": ErrorResponse, "description": "Access token not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def rotate_access_token(
    request: Request,
    token_id: SnowflakeIDParam,
    body: AccessTokenRotateRequest,
):
    """Rotate an API access token, cloning policy and scopes into a replacement.

    The new token value is always generated server-side; caller-supplied
    values are rejected.
    """
    check_host_restriction(request)
    admin_id = require_admin_permission(request, "admin.security.tokens")
    if body.token:
        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "code": 400,
                    "message": "Token values are generated server-side and cannot be supplied by clients",
                }
            },
        )
    from src.core import auth

    try:
        token = auth.rotate_api_access_token(
            token_id, admin_id, secrets.token_urlsafe(32)
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
    try:
        from src.utils.system_alerts import record_system_alert

        record_system_alert(
            "token_rotated",
            {"token_id": str(token_id), "rotated_by": str(admin_id)},
            source="admin",
            severity="info",
        )
    except Exception as alert_error:
        # The admin action already succeeded; a missing alert is not fatal.
        logger.debug(f"admin security alert failed: {alert_error}")

    return AccessTokenCreateResponse(
        token=token.token or "", access_token=_serialize_access_token(token)
    )


@router.post(
    "/security/access-tokens/{token_id}/scopes",
    response_model=AccessTokenScopeResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        404: {"model": ErrorResponse, "description": "Access token not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def add_access_token_scope(
    request: Request,
    token_id: SnowflakeIDParam,
    body: AccessTokenScopeCreateRequest,
):
    """Add an IP or CIDR scope rule to an API access token."""
    check_host_restriction(request)
    admin_id = require_admin_permission(request, "admin.security.tokens")
    from src.core import auth

    try:
        scope = auth.add_api_access_token_scope(
            token_id,
            body.scope_type,
            body.value,
            admin_id,
        )
    except ValueError as exc:
        if "not found" in str(exc).lower():
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": 404, "message": str(exc)}},
            )
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": str(exc)}},
        )
    return _serialize_access_token_scope(scope)


@router.delete(
    "/security/access-tokens/{token_id}/scopes/{scope_id}",
    response_model=SuccessResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        404: {"model": ErrorResponse, "description": "Access token scope not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def remove_access_token_scope(
    request: Request, token_id: SnowflakeIDParam, scope_id: SnowflakeIDParam
):
    """Remove an IP or CIDR scope rule from an API access token."""
    check_host_restriction(request)
    require_admin_permission(request, "admin.security.tokens")
    from src.core import auth

    success = auth.remove_api_access_token_scope(token_id, scope_id)
    if not success:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": 404, "message": "Access token scope not found"}},
        )
    return SuccessResponse(success=True, message=None)


@router.post(
    "/security/access-tokens/{token_id}/revoke",
    response_model=SuccessResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        404: {"model": ErrorResponse, "description": "Access token not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def revoke_access_token(request: Request, token_id: SnowflakeIDParam):
    """
    Revoke a specific API access token.
    """
    check_host_restriction(request)
    admin_id = require_admin_permission(request, "admin.security.tokens")
    from src.core import auth

    success = auth.revoke_api_access_token(token_id, admin_id)
    if not success:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": 404, "message": "Access token not found"}},
        )

    # Record as a system alert.
    try:
        from src.utils.system_alerts import record_system_alert

        record_system_alert(
            "token_revoked",
            {"token_id": str(token_id), "revoked_by": str(admin_id)},
            source="admin",
            severity="warning",
        )
    except Exception as alert_error:
        logger.debug(f"admin security warning alert failed: {alert_error}")

    return SuccessResponse(success=True, message=None)


@router.post(
    "/security/access-tokens/{token_id}/unrevoke",
    response_model=SuccessResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        404: {
            "model": ErrorResponse,
            "description": "Access token not found or not revoked",
        },
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def unrevoke_access_token(request: Request, token_id: SnowflakeIDParam):
    """
    Unrevoke a previously revoked API access token.
    """
    check_host_restriction(request)
    admin_id = require_admin_permission(request, "admin.security.tokens")
    from src.core import auth

    success = auth.unrevoke_api_access_token(token_id, admin_id)
    if not success:
        raise HTTPException(
            status_code=404,
            detail={
                "error": {
                    "code": 404,
                    "message": "Access token not found or not revoked",
                }
            },
        )

    # Record as a system alert.
    try:
        from src.utils.system_alerts import record_system_alert

        record_system_alert(
            "token_unrevoked",
            {"token_id": str(token_id), "unrevoked_by": str(admin_id)},
            source="admin",
            severity="info",
        )
    except Exception as alert_error:
        # The admin action already succeeded; a missing alert is not fatal.
        logger.debug(f"admin security alert failed: {alert_error}")

    return SuccessResponse(success=True, message=None)


@router.delete(
    "/security/unblock-ip/{ip_address:path}",
    response_model=SuccessResponse,
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def unblock_ip(request: Request, ip_address: str):
    """
    Remove an IP address from the blocklist.
    """
    check_host_restriction(request)
    require_admin_permission(request, "admin.security.ipblock")
    import ipaddress

    candidate = ip_address.strip()
    try:
        ipaddress.ip_address(candidate)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Invalid IP address"}},
        )
    from src.core import auth

    auth.unblock_ip(candidate)
    return SuccessResponse(success=True, message=None)


@router.get(
    "/security/banned-usernames",
    response_model=List[BannedUsernameResponse],
    responses={
        400: {"model": ErrorResponse, "description": "Invalid query parameters"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_banned_usernames(
    request: Request,
    limit: int = Query(200, ge=1, le=_MAX_SECURITY_LIST_LIMIT),
    offset: int = Query(0, ge=0, le=_MAX_SECURITY_LIST_OFFSET),
):
    """
    Retrieve a bounded page of banned username patterns.
    """
    check_host_restriction(request)
    require_admin_permission(request, "admin.security.usernames")
    from src.core import admin

    return [
        BannedUsernameResponse(**p.__dict__) if hasattr(p, "__dict__") else p
        for p in admin.get_banned_usernames(limit=limit, offset=offset)
    ]


@router.post(
    "/security/banned-usernames",
    response_model=SuccessResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def add_banned_username(request: Request, body: BannedUsernameCreate):
    """
    Add a new pattern to the list of banned usernames.
    """
    check_host_restriction(request)
    admin_id = require_admin_permission(request, "admin.security.usernames")
    from src.core import admin

    admin.add_banned_username(body.pattern, body.reason or "", admin_id, body.is_regex)
    return SuccessResponse(success=True, message=None)


@router.delete(
    "/security/banned-usernames/{pattern_id}",
    response_model=SuccessResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def remove_banned_username(request: Request, pattern_id: SnowflakeIDParam):
    """
    Remove a pattern from the list of banned usernames.
    """
    check_host_restriction(request)
    require_admin_permission(request, "admin.security.usernames")
    from src.core import admin

    admin.remove_banned_username(pattern_id)
    return SuccessResponse(success=True, message=None)


async def _close_revoked_user_gateway_connections(user_id: int, reason: str) -> None:
    """Server-side half of a per-user session revocation.

    A ``SECURITY_LOGOUT`` frame only asks a well-behaved client to disconnect;
    until the socket is actually closed it stays authenticated and usable on
    the server. The site-wide logout route already calls
    ``close_all_connections()`` -- per-user revocation (force-logout, account
    lock) must close that user's sockets too. Non-raising: the DB write that
    revoked the sessions has already succeeded, so a gateway failure must not
    turn a completed revocation into a 500.
    """
    try:
        from src.api.websocket import get_dispatcher, is_setup as ws_is_setup

        if ws_is_setup():
            await get_dispatcher().close_user_connections(
                user_id=user_id,
                reason=reason,
            )
    except Exception:
        logger.warning(
            "Failed to close gateway connections for revoked user %s",
            user_id,
            exc_info=True,
        )


@router.post(
    "/security/force-logout",
    response_model=SuccessResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid user ID"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def force_logout(request: Request, body: ForceLogoutRequest):
    """
    Immediately invalidate all active sessions for a specific user.
    """
    check_host_restriction(request)
    admin_id = require_admin_permission(request, "admin.security.sessions")
    try:
        uid = int(body.user_id)
        # force-logout revokes every session for its target, disabling that
        # account's access in exactly the same way as lock-user. It shares the
        # admin.security.sessions scope with lock-user/unlock-user, both of
        # which refuse an admin target at or above the actor's role position.
        # Without the same gate here, any holder of that scope could revoke a
        # superior admin's sessions (or its own), bypassing the hierarchy every
        # other admin-targeting mutation honours. Mirror lock/unlock: refuse
        # upward/equal/self targets and re-assert the hierarchy as the write
        # executes.
        import src.api as api

        from .users.admin_users import (
            _assert_hierarchy_at_write,
            _can_manage_admin_at,
            _load_admin_hierarchy,
        )

        db = api.get_db()
        if db is None:
            raise HTTPException(
                status_code=500,
                detail={"error": {"code": 500, "message": "Database not available"}},
            )
        hierarchy = _load_admin_hierarchy(db, int(admin_id), uid)
        if hierarchy["target_exists"] and not _can_manage_admin_at(
            int(admin_id), uid, hierarchy["current_pos"], hierarchy["target_pos"]
        ):
            raise HTTPException(
                status_code=403,
                detail={
                    "error": {
                        "code": 403,
                        "message": "Cannot force logout admins at or above your own position",
                    }
                },
            )
        if hierarchy["target_exists"]:
            _assert_hierarchy_at_write(db, int(admin_id), uid)

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
            logger.warning(
                "Failed to broadcast force-logout WebSocket event", exc_info=True
            )
        # The frame above is only a client-side hint: close the sockets on the
        # server as well, mirroring the site-wide logout route.
        await _close_revoked_user_gateway_connections(uid, "Security logout")

        try:
            from src.utils.system_alerts import record_system_alert

            record_system_alert(
                "user_sessions_killed",
                {"user_id": str(uid), "initiated_by": str(admin_id)},
                source="admin",
                severity="warning",
                target_path=f"#user-{uid}",
            )
        except Exception as alert_error:
            logger.debug(f"admin user alert failed: {alert_error}")

        return SuccessResponse(success=True, message=None)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Invalid user ID"}},
        )


@router.post(
    "/security/lock-user",
    response_model=SuccessResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid user ID"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def admin_lock_user(request: Request, body: UserLockRequest):
    """
    Lock a user account, preventing all access for a specified duration.
    """
    check_host_restriction(request)
    admin_id = require_admin_permission(request, "admin.security.sessions")
    try:
        uid = int(body.user_id)
        # Locking sets account_locked = 1 and logs out every session, i.e. it
        # disables the account. When the target is an admin this is an
        # admin-targeting mutation and must honour the same role-position
        # hierarchy as update/delete/toggle-status, ban/unban and
        # force-password-change. The scope alone is not enough: a low-position
        # holder of admin.security.sessions could otherwise disable a superior
        # admin (or its own account). Mirror force-purge: refuse upward/self
        # locks and re-assert the hierarchy as the write executes.
        import src.api as api

        from .users.admin_users import (
            _assert_hierarchy_at_write,
            _can_manage_admin_at,
            _load_admin_hierarchy,
        )

        db = api.get_db()
        if db is None:
            raise HTTPException(
                status_code=500,
                detail={"error": {"code": 500, "message": "Database not available"}},
            )
        hierarchy = _load_admin_hierarchy(db, int(admin_id), uid)
        if hierarchy["target_exists"] and not _can_manage_admin_at(
            int(admin_id), uid, hierarchy["current_pos"], hierarchy["target_pos"]
        ):
            raise HTTPException(
                status_code=403,
                detail={
                    "error": {
                        "code": 403,
                        "message": "Cannot lock admins at or above your own position",
                    }
                },
            )
        if hierarchy["target_exists"]:
            _assert_hierarchy_at_write(db, int(admin_id), uid)

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
            logger.warning(
                "Failed to broadcast lock-user WebSocket event", exc_info=True
            )
        # Locking disables the account and logs out every session -- revoke the
        # live gateway sockets too, not just the client-side logout hint.
        await _close_revoked_user_gateway_connections(uid, "Account suspended")

        try:
            from src.utils.system_alerts import record_system_alert

            record_system_alert(
                "user_suspended",
                {
                    "user_id": str(uid),
                    "duration_seconds": body.duration_seconds,
                    "suspended_by": str(admin_id),
                },
                source="admin",
                severity="warning",
                target_path=f"#user-{uid}",
            )
        except Exception as alert_error:
            logger.debug(f"admin user alert failed: {alert_error}")

        return SuccessResponse(success=True, message=None)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Invalid user ID"}},
        )


@router.post(
    "/security/unlock-user",
    response_model=SuccessResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid user ID"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def admin_unlock_user(request: Request, body: ForceLogoutRequest):
    """
    Unlock a previously locked user account.
    """
    check_host_restriction(request)
    admin_id = require_admin_permission(request, "admin.security.sessions")
    try:
        uid = int(body.user_id)
        # Unlocking clears account_locked for the target, making it the direct
        # inverse of lock-user. Every other admin-targeting mutation
        # (lock/update/delete/toggle-status, ban/unban, force-password-change,
        # force-purge) refuses a target at or above the actor's role position.
        # Without the same gate here, a low-position holder of
        # admin.security.sessions could re-enable a superior admin that a
        # higher-position admin disabled, or unlock its own account -- bypassing
        # the hierarchy. Mirror lock-user: refuse upward/equal/self unlocks and
        # re-assert the hierarchy as the write executes.
        import src.api as api

        from .users.admin_users import (
            _assert_hierarchy_at_write,
            _can_manage_admin_at,
            _load_admin_hierarchy,
        )

        db = api.get_db()
        if db is None:
            raise HTTPException(
                status_code=500,
                detail={"error": {"code": 500, "message": "Database not available"}},
            )
        hierarchy = _load_admin_hierarchy(db, int(admin_id), uid)
        if hierarchy["target_exists"] and not _can_manage_admin_at(
            int(admin_id), uid, hierarchy["current_pos"], hierarchy["target_pos"]
        ):
            raise HTTPException(
                status_code=403,
                detail={
                    "error": {
                        "code": 403,
                        "message": "Cannot unlock admins at or above your own position",
                    }
                },
            )
        if hierarchy["target_exists"]:
            _assert_hierarchy_at_write(db, int(admin_id), uid)

        from src.core import admin

        admin.unlock_user(uid)
        return SuccessResponse(success=True, message=None)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Invalid user ID"}},
        )


@router.post(
    "/security/logout-all",
    response_model=SuccessResponse,
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def logout_all_users(request: Request):
    """
    Invalidate all active sessions for every user on the platform.
    """
    check_host_restriction(request)
    admin_id = require_admin_permission(request, "admin.security.logout_all")
    from src.core import auth

    auth.logout_all_users()
    try:
        from src.api.websocket import get_dispatcher, is_setup as ws_is_setup

        if ws_is_setup():
            await get_dispatcher().close_all_connections(
                close_code=4004, reason="Security reset"
            )
    except Exception:
        logger.warning(
            "Failed to close WebSocket connections on logout-all", exc_info=True
        )

    try:
        from src.utils.system_alerts import record_system_alert

        record_system_alert(
            "global_session_purge",
            {"initiated_by": str(admin_id)},
            source="admin",
            severity="warning",
        )
    except Exception as alert_error:
        logger.debug(f"admin security warning alert failed: {alert_error}")

    return SuccessResponse(success=True, message=None)
