from typing import Optional
from fastapi import APIRouter, HTTPException, Depends, Query

import src.api as api
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.servers import (
    MemberResponse,
    MemberListResponse,
    PresenceResponse,
    MemberUpdateRequest,
    KickMemberRequest,
)
from src.api.schemas.common import SnowflakeID, ErrorResponse, SuccessResponse
from src.core.database import cached, invalidate_pattern

import utils.logger as logger
from .roles import _invalidate_roles_listing_cache

router = APIRouter()


def _invalidate_member_access_caches() -> None:
    """Drop API-layer caches that would keep serving data to a removed member.

    The manager clears its own membership caches when a user is kicked, banned
    or leaves, but this router keeps three additional per-viewer response
    caches (member list ``server_members_api``, joined servers
    ``user_servers_api`` and channel list ``server_channels_api``). All three
    are keyed by the authenticated user, so without an explicit invalidation a
    removed member keeps reading cached rosters/channels through a valid 200
    response until the 30s TTL expires. Membership revocation must be visible
    at the HTTP boundary immediately, not just in the core.
    """
    invalidate_pattern("server_members_api:*")
    invalidate_pattern("user_servers_api:*")
    invalidate_pattern("server_channels_api:*")


def _resolve_member_user_id(db, server_id: int, member_or_user_id: int):
    """Resolve a path id (member id or user id) to a user id in one query.

    All four member-mutation routes accept either a ``srv_members.id`` or a
    ``user_id`` in the path and previously issued two sequential ``fetch_one``
    calls to work out which: first keyed by ``id``, then a fallback keyed by
    ``user_id``. A single query returns the same row while halving the database
    round-trips on these hot endpoints.

    Each disjunct restates the ``server_id`` predicate so SQLite's multi-index
    OR planner uses two point lookups (primary key + ``(server_id, user_id)``
    unique index) instead of scanning every row in the server. ``ORDER BY
    (id = ?) DESC`` preserves the original precedence for the (anomalous) case
    where both a member-id and a user-id row match the same number.
    """
    return db.fetch_one(
        "SELECT user_id FROM srv_members "
        "WHERE (id = ? AND server_id = ?) OR (server_id = ? AND user_id = ?) "
        "ORDER BY (id = ?) DESC LIMIT 1",
        (
            member_or_user_id,
            server_id,
            server_id,
            member_or_user_id,
            member_or_user_id,
        ),
    )


def _unique_member_user_ids(members) -> list:
    """Collect distinct user ids from member rows, preserving first-seen order.

    A server can carry more than one row for a user (temporary memberships),
    and the bulk user/presence lookups below are keyed by user id. Deduplicating
    here keeps those bulk calls proportional to distinct users rather than to
    raw membership rows.
    """
    user_ids = []
    seen = set()
    for m in members:
        uid = getattr(m, "user_id", None) or m.get("user_id")
        if uid and uid not in seen:
            seen.add(uid)
            user_ids.append(uid)
    return user_ids


@router.get(
    "/{server_id}/members",
    response_model=MemberListResponse,
    summary="List server members",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid server ID"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {"model": ErrorResponse, "description": "Access denied"},
        404: {"model": ErrorResponse, "description": "Server not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
@cached(ttl=30, prefix="server_members_api")
def get_server_members(
    server_id: str,
    limit: int = Query(100, ge=1, le=200),
    after: Optional[str] = None,
    current_user: TokenInfo = Depends(get_current_user),
) -> MemberListResponse:
    servers_mod = api.get_servers()
    auth = api.get_auth()
    presence = api.get_presence()

    if not servers_mod:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Servers module not available"}},
        )

    try:
        try:
            sid = int(server_id)
        except (ValueError, TypeError):
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid server ID"}},
            )

        after_id: Optional[int] = None
        if after is not None:
            try:
                after_id = int(after)
            except (ValueError, TypeError):
                raise HTTPException(
                    status_code=400,
                    detail={"error": {"code": 400, "message": "Invalid after cursor"}},
                )

        curr_user_id = (
            current_user.user_id
            if hasattr(current_user, "user_id")
            else getattr(current_user, "get", lambda k: None)("user_id")
        )

        # Fetch one extra row to compute has_more without a second query.
        members = servers_mod.get_members(curr_user_id, sid, limit + 1, after_id)
        if members is None:
            raise HTTPException(
                status_code=500,
                detail={"error": {"code": 500, "message": "Failed to fetch members"}},
            )
        has_more = len(members) > limit
        if has_more:
            members = members[:limit]
        if len(members) == 0:
            return MemberListResponse(items=[], has_more=False)

        user_ids = _unique_member_user_ids(members)

        users_map = {}
        if auth and user_ids:
            users_map = auth.get_users_bulk(user_ids)

        presence_map = {}
        if presence and user_ids:
            try:
                presence_map = presence.get_visible_presences_bulk(
                    curr_user_id, user_ids
                )
            except Exception as e:
                logger.warning(f"Failed to get bulk presence for server {sid}: {e}")

        result = []
        for m in members:
            if isinstance(m, dict):
                raw_member_id = m.get("id") or m.get("member_id")
                raw_user_id = m.get("user_id", 0)
                nickname = m.get("nickname")
                joined_at = m.get("joined_at")
                roles = m.get("roles", [])
                timeout_until = m.get("timeout_until")
                timeout_reason = m.get("timeout_reason")
            else:
                raw_member_id = getattr(m, "id", None)
                raw_user_id = getattr(m, "user_id", 0)
                nickname = getattr(m, "nickname", None)
                joined_at = getattr(m, "joined_at", None)
                roles = getattr(m, "roles", [])
                timeout_until = getattr(m, "timeout_until", None)
                timeout_reason = getattr(m, "timeout_reason", None)

            user_id = int(raw_user_id)
            user = users_map.get(user_id) or users_map.get(str(user_id))

            pres = presence_map.get(user_id) or presence_map.get(str(user_id))
            presence_data = PresenceResponse(status="offline")
            if pres:
                status = getattr(pres, "status", None)
                if status and hasattr(status, "value"):
                    status = status.value
                presence_data = PresenceResponse(
                    status=str(status) if status else "offline"
                )

            result.append(
                MemberResponse(
                    member_id=SnowflakeID(int(raw_member_id))
                    if raw_member_id
                    else None,
                    user_id=SnowflakeID(user_id),
                    username=str(user.username) if user else f"User {user_id}",
                    nickname=nickname,
                    avatar_url=getattr(user, "avatar_url", None),
                    joined_at=joined_at,
                    roles=[SnowflakeID(r) for r in (roles or [])],
                    presence=presence_data,
                    badges=getattr(user, "badges", []),
                    timeout_until=timeout_until,
                    timeout_reason=timeout_reason,
                )
            )
        return MemberListResponse(items=result, has_more=has_more)
    except HTTPException:
        raise
    except Exception as e:
        exc_name = type(e).__name__
        if "NotFound" in exc_name:
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": 404, "message": "Server not found"}},
            )
        elif "Access" in exc_name:
            raise HTTPException(
                status_code=403,
                detail={"error": {"code": 403, "message": "Access denied"}},
            )

        logger.error(
            f"Failed to fetch members for server {server_id}: {e}", exc_info=True
        )
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.patch(
    "/{server_id}/members/{member_id}",
    response_model=MemberResponse,
    summary="Update a server member",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid ID"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {"model": ErrorResponse, "description": "Access denied"},
        404: {"model": ErrorResponse, "description": "Member not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def update_server_member(
    server_id: str,
    member_id: str,
    payload: MemberUpdateRequest,
    current_user: TokenInfo = Depends(get_current_user),
) -> MemberResponse:
    servers_mod = api.get_servers()
    if not servers_mod:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Servers module not available"}},
        )

    try:
        try:
            sid = int(server_id)
            mid = int(member_id)
        except (ValueError, TypeError):
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid ID"}},
            )

        db = api.get_db()
        if not db:
            raise HTTPException(
                status_code=500,
                detail={"error": {"code": 500, "message": "Database not available"}},
            )
        row = _resolve_member_user_id(db, sid, mid)
        if not row:
            raise HTTPException(status_code=404, detail="Member not found")

        target_user_id = row["user_id"]

        member = servers_mod.update_member(
            current_user.user_id,
            sid,
            target_user_id,
            nickname=payload.nickname,
            muted=payload.muted,
            deafened=payload.deafened,
            timeout_until=payload.timeout_until,
            timeout_reason=payload.timeout_reason,
        )

        user = None
        auth = api.get_auth()
        if auth:
            users_map = auth.get_users_bulk([target_user_id])
            user = users_map.get(target_user_id) or users_map.get(str(target_user_id))

        return MemberResponse(
            member_id=SnowflakeID(int(member.id)),
            user_id=SnowflakeID(int(member.user_id)),
            username=str(getattr(user, "username", None) or f"User {member.user_id}"),
            nickname=member.nickname,
            avatar_url=getattr(user, "avatar_url", None),
            joined_at=member.joined_at,
            roles=[SnowflakeID(r) for r in (member.roles or [])],
            timeout_until=member.timeout_until,
            timeout_reason=member.timeout_reason,
        )
    except HTTPException:
        raise
    except Exception as e:
        exc_name = type(e).__name__
        if "NotFound" in exc_name or "Exists" in exc_name:
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": 404, "message": "Member not found"}},
            )
        elif "Permission" in exc_name or "Hierarchy" in exc_name or "Owner" in exc_name:
            raise HTTPException(
                status_code=403,
                detail={"error": {"code": 403, "message": "Access denied"}},
            )

        logger.error(
            f"Failed to update member {member_id} in server {server_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.delete(
    "/{server_id}/members/{member_id}",
    response_model=SuccessResponse,
    summary="Kick member",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid ID"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {"model": ErrorResponse, "description": "Access denied"},
        404: {"model": ErrorResponse, "description": "Member not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def kick_member(
    server_id: str,
    member_id: str,
    current_user: TokenInfo = Depends(get_current_user),
    payload: Optional[KickMemberRequest] = None,
) -> SuccessResponse:
    servers_mod = api.get_servers()
    if not servers_mod:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Servers module not available"}},
        )

    try:
        try:
            sid = int(server_id)
            mid = int(member_id)
        except (ValueError, TypeError):
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid ID"}},
            )
        db = api.get_db()
        if not db:
            raise HTTPException(
                status_code=500,
                detail={"error": {"code": 500, "message": "Database not available"}},
            )
        row = _resolve_member_user_id(db, sid, mid)
        if not row:
            raise HTTPException(status_code=404, detail="Member not found")

        target_user_id = row["user_id"]
        reason = getattr(payload, "reason", None) if payload else None
        servers_mod.kick_member(current_user.user_id, sid, target_user_id, reason)
        # The kick is committed: the target must lose cached API access now.
        _invalidate_member_access_caches()
        return SuccessResponse(success=True, message=None)
    except HTTPException:
        raise
    except Exception as e:
        exc_name = type(e).__name__
        if "NotFound" in exc_name:
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": 404, "message": "Member not found"}},
            )
        elif "Permission" in exc_name or "Hierarchy" in exc_name or "Owner" in exc_name:
            raise HTTPException(
                status_code=403,
                detail={"error": {"code": 403, "message": "Access denied"}},
            )

        logger.error(
            f"Failed to kick member {member_id} from server {server_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.put(
    "/{server_id}/members/{member_id}/roles/{role_id}",
    response_model=SuccessResponse,
    summary="Add role to member",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid ID"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {"model": ErrorResponse, "description": "Access denied"},
        404: {"model": ErrorResponse, "description": "Member or role not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def assign_role_to_member(
    server_id: str,
    member_id: str,
    role_id: str,
    current_user: TokenInfo = Depends(get_current_user),
) -> SuccessResponse:
    servers_mod = api.get_servers()
    if not servers_mod:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Servers module not available"}},
        )

    try:
        try:
            sid = int(server_id)
            mid = int(member_id)
            rid = int(role_id)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid ID"}},
            )

        db = api.get_db()
        if not db:
            raise HTTPException(
                status_code=500,
                detail={"error": {"code": 500, "message": "Database not available"}},
            )
        row = _resolve_member_user_id(db, sid, mid)
        if not row:
            raise HTTPException(status_code=404, detail="Member not found")

        target_user_id = row["user_id"]

        servers_mod.assign_role(current_user.user_id, sid, target_user_id, rid)
        # A role change can grant the target ``roles.manage``; the per-user
        # cached role listing must not keep returning a stale authorization
        # decision (or stale role data) for the rest of its 300s TTL.
        _invalidate_roles_listing_cache()
        return SuccessResponse(success=True, message=None)
    except HTTPException:
        raise
    except Exception as e:
        exc_name = type(e).__name__
        if "NotFound" in exc_name:
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": 404, "message": "Member or role not found"}},
            )
        # Must precede the generic "Permission" match: "InvalidPermissionKeyError"
        # contains "Permission", so the 400 branch was otherwise unreachable and
        # an unknown permission key surfaced as a misleading 403.
        elif "InvalidPermissionKey" in exc_name:
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid request"}},
            )
        elif "Permission" in exc_name or "Hierarchy" in exc_name:
            raise HTTPException(
                status_code=403,
                detail={"error": {"code": 403, "message": "Access denied"}},
            )
        elif "Exists" in exc_name:
            return SuccessResponse(success=True, message=None)

        logger.error(
            f"Failed to assign role {role_id} to member {member_id} in server {server_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.delete(
    "/{server_id}/members/{member_id}/roles/{role_id}",
    response_model=SuccessResponse,
    summary="Remove role from member",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid ID"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {"model": ErrorResponse, "description": "Access denied"},
        404: {"model": ErrorResponse, "description": "Member or role not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def remove_role_from_member(
    server_id: str,
    member_id: str,
    role_id: str,
    current_user: TokenInfo = Depends(get_current_user),
) -> SuccessResponse:
    servers_mod = api.get_servers()
    if not servers_mod:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Servers module not available"}},
        )

    try:
        try:
            sid = int(server_id)
            mid = int(member_id)
            rid = int(role_id)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid ID"}},
            )

        db = api.get_db()
        if not db:
            raise HTTPException(
                status_code=500,
                detail={"error": {"code": 500, "message": "Database not available"}},
            )
        row = _resolve_member_user_id(db, sid, mid)
        if not row:
            raise HTTPException(status_code=404, detail="Member not found")

        target_user_id = row["user_id"]

        servers_mod.remove_role(current_user.user_id, sid, target_user_id, rid)
        # Revoking a role can remove ``roles.manage``; drop the cached role
        # listing so the former holder cannot keep reading it from cache.
        _invalidate_roles_listing_cache()
        return SuccessResponse(success=True, message=None)
    except HTTPException:
        raise
    except Exception as e:
        exc_name = type(e).__name__
        if "NotFound" in exc_name:
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": 404, "message": "Member or role not found"}},
            )
        elif (
            "Permission" in exc_name or "Hierarchy" in exc_name or "Default" in exc_name
        ):
            raise HTTPException(
                status_code=403,
                detail={"error": {"code": 403, "message": "Access denied"}},
            )

        logger.error(
            f"Failed to remove role {role_id} from member {member_id} in server {server_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )
