"""
Server routes - Server/guild management endpoints.
"""

from typing import List, Optional, Dict, Any
from fastapi import APIRouter, HTTPException, Depends, File, UploadFile

import src.api as api
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.servers import (
    ServerCreateRequest,
    ServerUpdateRequest,
    ServerResponse,
    ChannelResponse,
    ChannelCreateRequest,
    MemberResponse,
    PresenceResponse,
    RoleResponse,
    RoleCreateRequest,
    RoleUpdateRequest,
    InviteResponse,
    BanResponse,
    BanCreateRequest,
    AuditLogEntryResponse,
    WebhookResponse,
    AutomodRuleResponse,
    AutomodRuleCreateRequest,
    AutomodRuleUpdateRequest,
    AutomodViolationResponse,
)
from src.api.schemas.common import SnowflakeID, ErrorResponse, SuccessResponse
from src.core.servers.models import ChannelType
from src.core.database import (
    invalidate_user_servers,
    invalidate_server_channels,
    invalidate_server,
    cached,
)

import utils.logger as logger
from .helpers import (
    _server_to_response,
    _channel_to_response,
    _automod_rule_to_response,
)

router = APIRouter(tags=["Servers"])


@router.get(
    "",
    response_model=List[ServerResponse],
    summary="List joined servers",
    responses={
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
@cached(ttl=30, prefix="user_servers_api")
def get_servers(
    current_user: TokenInfo = Depends(get_current_user),
) -> List[ServerResponse]:
    """
    Get all servers the user is a member of.

    Returns a list of servers the authenticated user belongs to.
    """
    servers_mod = api.get_servers()
    if not servers_mod:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Servers module not available"}},
        )

    try:
        servers = servers_mod.get_servers(current_user.user_id)
        if servers is None:
            raise HTTPException(
                status_code=500,
                detail={"error": {"code": 500, "message": "Failed to fetch servers"}},
            )
        return [_server_to_response(s) for s in servers]
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Failed to fetch servers for user {current_user.user_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.post(
    "",
    response_model=ServerResponse,
    summary="Create server",
    responses={
        400: {"model": ErrorResponse, "description": "Limit reached or invalid data"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def create_server(
    body: ServerCreateRequest, current_user: TokenInfo = Depends(get_current_user)
) -> ServerResponse:
    """
    Create a new server.

    Creates a server with the authenticated user as owner.
    """
    servers_mod = api.get_servers()
    if not servers_mod:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Servers module not available"}},
        )

    try:
        server = servers_mod.create_server(
            owner_id=current_user.user_id, name=body.name, description=body.description
        )
        # Invalidate user's server list cache
        invalidate_user_servers(current_user.user_id)
        return _server_to_response(server)
    except Exception as e:
        exc_name = type(e).__name__
        if "Limit" in exc_name:
            raise HTTPException(
                status_code=400, detail={"error": {"code": 400, "message": str(e)}}
            )

        logger.error(
            f"Failed to create server for user {current_user.user_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.get(
    "/{server_id}",
    response_model=ServerResponse,
    summary="Get server",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid server ID"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {"model": ErrorResponse, "description": "Access denied"},
        404: {"model": ErrorResponse, "description": "Server not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
@cached(ttl=60)
async def get_server(
    server_id: str, current_user: TokenInfo = Depends(get_current_user)
) -> ServerResponse:
    """
    Get server by ID.

    Returns server information if the user is a member.
    """
    servers_mod = api.get_servers()
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

        server = servers_mod.get_server(sid, current_user.user_id)
        if not server:
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": 404, "message": "Server not found"}},
            )
        return _server_to_response(server)
    except HTTPException:
        raise
    except Exception as e:
        exc_name = type(e).__name__
        if "NotFound" in exc_name:
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": 404, "message": "Server not found"}},
            )
        elif "Access" in exc_name or "Permission" in exc_name:
            raise HTTPException(
                status_code=403,
                detail={"error": {"code": 403, "message": "Access denied"}},
            )

        logger.error(f"Failed to get server {server_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.patch(
    "/{server_id}",
    response_model=ServerResponse,
    summary="Update server",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid server ID or data"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {"model": ErrorResponse, "description": "Access denied"},
        404: {"model": ErrorResponse, "description": "Server not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def update_server(
    server_id: str,
    body: ServerUpdateRequest,
    current_user: TokenInfo = Depends(get_current_user),
) -> ServerResponse:
    """
    Update server settings.

    Updates server information. Requires manage server permission.
    """
    servers_mod = api.get_servers()
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

        update_data = body.model_dump(exclude_unset=True)
        server = servers_mod.update_server(current_user.user_id, sid, **update_data)
        # Invalidate channel list cache if default channel changed
        if "default_channel_id" in update_data:
            invalidate_server_channels(sid)
        # Invalidate server list cache for all members
        invalidate_server(sid)
        return _server_to_response(server)
    except HTTPException:
        raise
    except Exception as e:
        exc_name = type(e).__name__
        if "NotFound" in exc_name:
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": 404, "message": "Server not found"}},
            )
        elif "Permission" in exc_name:
            raise HTTPException(
                status_code=403, detail={"error": {"code": 403, "message": str(e)}}
            )

        logger.error(f"Failed to update server {server_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.delete(
    "/{server_id}",
    response_model=SuccessResponse,
    summary="Delete server",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid server ID"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {"model": ErrorResponse, "description": "Access denied"},
        404: {"model": ErrorResponse, "description": "Server not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def delete_server(
    server_id: str, current_user: TokenInfo = Depends(get_current_user)
) -> SuccessResponse:
    """
    Delete a server.

    Permanently deletes the server. Only the owner can delete.
    """
    servers_mod = api.get_servers()
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

        servers_mod.delete_server(current_user.user_id, sid)
        # Invalidate user's server list cache
        invalidate_user_servers(current_user.user_id)
        return SuccessResponse(success=True)
    except HTTPException:
        raise
    except Exception as e:
        exc_name = type(e).__name__
        if "NotFound" in exc_name:
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": 404, "message": "Server not found"}},
            )
        elif "Permission" in exc_name or "Owner" in exc_name:
            raise HTTPException(
                status_code=403, detail={"error": {"code": 403, "message": str(e)}}
            )

        logger.error(f"Failed to delete server {server_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.get(
    "/{server_id}/channels",
    response_model=List[ChannelResponse],
    summary="List server channels",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid server ID"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {"model": ErrorResponse, "description": "Access denied"},
        404: {"model": ErrorResponse, "description": "Server not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
@cached(ttl=30, prefix="server_channels_api")
def get_server_channels(
    server_id: str, current_user: TokenInfo = Depends(get_current_user)
) -> List[ChannelResponse]:
    """
    Get all channels in a server.

    Returns channels the user has access to view.
    """
    servers_mod = api.get_servers()
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

        exists_row = servers_mod._get_manager()._db.fetch_one(
            "SELECT 1 FROM srv_servers WHERE id = ? AND deleted = 0", (sid,)
        )
        if not exists_row:
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": 404, "message": "Server not found"}},
            )

        channels = servers_mod.get_channels(current_user.user_id, sid)
        if channels is None:
            raise HTTPException(
                status_code=500,
                detail={"error": {"code": 500, "message": "Failed to fetch channels"}},
            )
        return [_channel_to_response(c) for c in channels]
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
            f"Failed to fetch channels for server {server_id}: {e}", exc_info=True
        )
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.get(
    "/{server_id}/members",
    response_model=List[MemberResponse],
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
    limit: int = 100,
    after: Optional[str] = None,
    current_user: TokenInfo = Depends(get_current_user),
) -> List[MemberResponse]:
    """Get server members (cached for 30s)."""
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

        # Reconstruct TokenInfo if it's a dict from cache
        curr_user_id = (
            current_user.user_id
            if hasattr(current_user, "user_id")
            else getattr(current_user, "get", lambda k: None)("user_id")
        )

        members = servers_mod.get_members(curr_user_id, sid)
        if members is None:
            raise HTTPException(
                status_code=500,
                detail={"error": {"code": 500, "message": "Failed to fetch members"}},
            )
        if len(members) == 0:
            return []

        # Get all user IDs for bulk fetching, handling both objects and dicts
        user_ids = []
        for m in members:
            uid = getattr(m, "user_id", None) or m.get("user_id")
            if uid:
                user_ids.append(uid)

        # Bulk fetch user data
        users_map = {}
        if auth and user_ids:
            users_map = auth.get_users_bulk(user_ids)

        # Bulk fetch presence data
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
            # Handle both objects and dicts (from cache)
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

            # Robust lookup: check both string and int keys
            user_id = int(raw_user_id)
            user = users_map.get(user_id) or users_map.get(str(user_id))

            # Presence info
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
        return result
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
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )


# ==================== Member Management ====================


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
    server_id: str, member_id: str, current_user: TokenInfo = Depends(get_current_user)
) -> SuccessResponse:
    """
    Kick a member from a server.

    Removes the member from the server. Requires kick members permission.
    """
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
        row = db.fetch_one("SELECT user_id FROM srv_members WHERE id = ?", (mid,))
        if not row:
            row = db.fetch_one(
                "SELECT user_id FROM srv_members WHERE server_id = ? AND user_id = ?",
                (sid, mid),
            )
        if not row:
            raise HTTPException(status_code=404, detail="Member not found")

        target_user_id = row["user_id"]
        servers_mod.kick_member(current_user.user_id, sid, target_user_id)
        return SuccessResponse(success=True)
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
                status_code=403, detail={"error": {"code": 403, "message": str(e)}}
            )

        logger.error(
            f"Failed to kick member {member_id} from server {server_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
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
    """
    Assign a role to a member.

    Adds the specified role to the member. Requires manage roles permission.
    """
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

        # The API receives member_id but the core method expects member_user_id
        # We need to resolve member_id to user_id
        db = api.get_db()
        if not db:
            raise HTTPException(
                status_code=500,
                detail={"error": {"code": 500, "message": "Database not available"}},
            )
        row = db.fetch_one("SELECT user_id FROM srv_members WHERE id = ?", (mid,))
        if not row:
            row = db.fetch_one(
                "SELECT user_id FROM srv_members WHERE server_id = ? AND user_id = ?",
                (sid, mid),
            )
        if not row:
            raise HTTPException(status_code=404, detail="Member not found")

        target_user_id = row["user_id"]

        servers_mod.assign_role(current_user.user_id, sid, target_user_id, rid)
        return SuccessResponse(success=True)
    except HTTPException:
        raise
    except Exception as e:
        exc_name = type(e).__name__
        if "NotFound" in exc_name:
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": 404, "message": "Member or role not found"}},
            )
        elif "Permission" in exc_name or "Hierarchy" in exc_name:
            raise HTTPException(
                status_code=403, detail={"error": {"code": 403, "message": str(e)}}
            )
        elif "Exists" in exc_name:
            return SuccessResponse(success=True)  # Already has role, treat as success

        logger.error(
            f"Failed to assign role {role_id} to member {member_id} in server {server_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
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
    """
    Remove a role from a member.

    Removes the specified role from the member. Requires manage roles permission.
    """
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

        # Resolve member_id to user_id
        db = api.get_db()
        if not db:
            raise HTTPException(
                status_code=500,
                detail={"error": {"code": 500, "message": "Database not available"}},
            )
        row = db.fetch_one("SELECT user_id FROM srv_members WHERE id = ?", (mid,))
        if not row:
            row = db.fetch_one(
                "SELECT user_id FROM srv_members WHERE server_id = ? AND user_id = ?",
                (sid, mid),
            )
        if not row:
            raise HTTPException(status_code=404, detail="Member not found")

        target_user_id = row["user_id"]

        servers_mod.remove_role(current_user.user_id, sid, target_user_id, rid)
        return SuccessResponse(success=True)
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
                status_code=403, detail={"error": {"code": 403, "message": str(e)}}
            )

        logger.error(
            f"Failed to remove role {role_id} from member {member_id} in server {server_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )


# ==================== Channel Management ====================


@router.post(
    "/{server_id}/channels",
    response_model=ChannelResponse,
    summary="Create channel",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid server ID or data"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {"model": ErrorResponse, "description": "Access denied"},
        404: {"model": ErrorResponse, "description": "Server not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def create_server_channel(
    server_id: str,
    body: ChannelCreateRequest,
    current_user: TokenInfo = Depends(get_current_user),
) -> ChannelResponse:
    """Create a channel in a server."""
    servers_mod = api.get_servers()
    if not servers_mod:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Servers module not available"}},
        )

    try:
        sid = int(server_id)
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Invalid server ID"}},
        )

    # Build kwargs, only including supported parameters
    kwargs = {
        "user_id": current_user.user_id,
        "server_id": sid,
        "name": body.name,
    }
    # Handle both 'type' and 'channel_type' for backward compatibility
    type_val = getattr(body, "channel_type", None) or getattr(body, "type", None)
    if type_val:
        # Convert string to ChannelType enum
        type_str = type_val.lower()
        try:
            kwargs["channel_type"] = ChannelType(type_str)
        except ValueError:
            kwargs["channel_type"] = ChannelType.TEXT

    if body.topic:
        kwargs["topic"] = body.topic
    if body.category_id:
        kwargs["category_id"] = int(body.category_id)
    if body.nsfw is not None:
        kwargs["nsfw"] = body.nsfw
    if body.slowmode_seconds is not None:
        kwargs["slowmode_seconds"] = body.slowmode_seconds
    if body.read_receipts_enabled is not None:
        kwargs["read_receipts_enabled"] = body.read_receipts_enabled

    try:
        channel = servers_mod.create_channel(**kwargs)
        # Invalidate server's channel list cache
        invalidate_server_channels(sid)

        response = _channel_to_response(channel)

        # Broadcast CHANNEL_CREATE event via WebSocket (fire and forget)
        import asyncio

        async def dispatch_channel_create():
            try:
                from src.api.websocket import get_dispatcher, is_setup as ws_is_setup
                from src.core.events.models import Event
                from src.core.events.types import EventType

                if ws_is_setup():
                    dispatcher = get_dispatcher()
                    user_ids = servers_mod.get_member_user_ids(sid)

                    if user_ids:
                        event = Event(
                            event_type=EventType.CHANNEL_CREATE,
                            data=response.model_dump(),
                            server_id=sid,
                        )
                        await dispatcher.dispatch_event(event, user_ids)
            except Exception as e:
                logger.debug(f"Failed to broadcast CHANNEL_CREATE: {e}")

        asyncio.create_task(dispatch_channel_create())

        return response
    except HTTPException:
        raise
    except TypeError as e:
        # Handle unexpected keyword arguments by trying simpler call
        if "unexpected keyword argument" in str(e):
            try:
                channel = servers_mod.create_channel(
                    user_id=current_user.user_id, server_id=sid, name=body.name
                )
                # Invalidate server's channel list cache
                invalidate_server_channels(sid)
                return _channel_to_response(channel)
            except Exception as inner_e:
                logger.error(
                    f"Failed to create channel in server {server_id} (fallback): {inner_e}",
                    exc_info=True,
                )
                raise HTTPException(
                    status_code=500,
                    detail={"error": {"code": 500, "message": str(inner_e)}},
                )

        logger.error(
            f"Failed to create channel in server {server_id}: {e}", exc_info=True
        )
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )
    except Exception as e:
        exc_name = type(e).__name__
        if "NotFound" in exc_name:
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": 404, "message": "Server not found"}},
            )
        elif "Permission" in exc_name:
            raise HTTPException(
                status_code=403, detail={"error": {"code": 403, "message": str(e)}}
            )

        logger.error(
            f"Failed to create channel in server {server_id}: {e}", exc_info=True
        )
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )


@router.get(
    "/{server_id}/invites",
    response_model=List[InviteResponse],
    summary="List server invites",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid server ID"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {"model": ErrorResponse, "description": "Access denied"},
        404: {"model": ErrorResponse, "description": "Server not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_server_invites(
    server_id: str, current_user: TokenInfo = Depends(get_current_user)
) -> List[InviteResponse]:
    """Get all invites for a server."""
    servers_mod = api.get_servers()
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

        invites = servers_mod.get_invites(current_user.user_id, sid)
        return [
            InviteResponse(
                code=inv.code,
                server_id=SnowflakeID(inv.server_id),
                channel_id=SnowflakeID(inv.channel_id)
                if hasattr(inv, "channel_id")
                else None,
                inviter_id=SnowflakeID(inv.inviter_id)
                if hasattr(inv, "inviter_id")
                else None,
                uses=getattr(inv, "uses", 0),
                max_uses=getattr(inv, "max_uses", 0),
                max_age=getattr(inv, "max_age", 86400),
                temporary=getattr(inv, "temporary", False),
                created_at=getattr(inv, "created_at", 0),
                expires_at=getattr(inv, "expires_at", None),
            )
            for inv in (invites or [])
        ]
    except HTTPException:
        raise
    except Exception as e:
        exc_name = type(e).__name__
        if "NotFound" in exc_name:
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": 404, "message": "Server not found"}},
            )
        elif "Permission" in exc_name:
            raise HTTPException(
                status_code=403, detail={"error": {"code": 403, "message": str(e)}}
            )

        logger.error(
            f"Failed to fetch invites for server {server_id}: {e}", exc_info=True
        )
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )


# ==================== Role Management ====================


@router.get(
    "/{server_id}/roles",
    response_model=List[RoleResponse],
    summary="List server roles",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid server ID"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {"model": ErrorResponse, "description": "Access denied"},
        404: {"model": ErrorResponse, "description": "Server not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
@cached(ttl=300)
async def get_server_roles(
    server_id: str, current_user: TokenInfo = Depends(get_current_user)
) -> List[RoleResponse]:
    """Get all roles in a server."""
    servers_mod = api.get_servers()
    if not servers_mod:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Servers module not available"}},
        )

    try:
        sid = int(server_id)
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Invalid server ID"}},
        )

    try:
        roles = servers_mod.get_roles(current_user.user_id, sid)
        return [
            RoleResponse(
                id=SnowflakeID(r.id),
                server_id=SnowflakeID(sid),
                name=r.name,
                color=getattr(r, "color", None),
                position=getattr(r, "position", 0),
                permissions=getattr(r, "permissions", {}),
                hoist=getattr(r, "hoist", False),
                mentionable=getattr(r, "mentionable", False),
                is_default=getattr(r, "is_default", False),
            )
            for r in (roles or [])
        ]
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
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )


@router.post(
    "/{server_id}/roles",
    response_model=RoleResponse,
    summary="Create role",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid server ID or data"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {"model": ErrorResponse, "description": "Access denied"},
        404: {"model": ErrorResponse, "description": "Server not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def create_role(
    server_id: str,
    body: RoleCreateRequest,
    current_user: TokenInfo = Depends(get_current_user),
) -> RoleResponse:
    """
    Create a new role in a server.

    Creates a new role with specified permissions. Requires manage roles permission.
    """
    servers_mod = api.get_servers()
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

        servers_mod.require_permission(current_user.user_id, sid, "roles.manage")
        role = servers_mod.create_role(
            user_id=current_user.user_id,
            server_id=sid,
            name=body.name,
            color=body.color,
            permissions=body.permissions,
            hoist=body.hoist,
            mentionable=body.mentionable,
        )
        return RoleResponse(
            id=SnowflakeID(role.id),
            server_id=SnowflakeID(sid),
            name=role.name,
            color=getattr(role, "color", None),
            position=getattr(role, "position", 0),
            permissions=getattr(role, "permissions", {}),
            hoist=getattr(role, "hoist", False),
            mentionable=getattr(role, "mentionable", False),
        )
    except HTTPException:
        raise
    except Exception as e:
        exc_name = type(e).__name__
        if "NotFound" in exc_name:
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": 404, "message": "Server not found"}},
            )
        elif "Permission" in exc_name:
            raise HTTPException(
                status_code=403, detail={"error": {"code": 403, "message": str(e)}}
            )

        logger.error(f"Failed to create role in server {server_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )


@router.patch(
    "/{server_id}/roles/{role_id}",
    response_model=RoleResponse,
    summary="Update role",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid ID or data"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {"model": ErrorResponse, "description": "Access denied"},
        404: {"model": ErrorResponse, "description": "Role not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def update_role(
    server_id: str,
    role_id: str,
    body: RoleUpdateRequest,
    current_user: TokenInfo = Depends(get_current_user),
) -> RoleResponse:
    """
    Update a role in a server.

    Modifies an existing role. Requires manage roles permission.
    """
    servers_mod = api.get_servers()
    if not servers_mod:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Servers module not available"}},
        )

    try:
        try:
            sid = int(server_id)
            rid = int(role_id)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid ID"}},
            )

        servers_mod.require_permission(current_user.user_id, sid, "roles.manage")
        # Note: manager.update_role doesn't need server_id as role_id is unique
        update_data = body.model_dump(exclude_unset=True)
        role = servers_mod.update_role(current_user.user_id, rid, **update_data)
        return RoleResponse(
            id=SnowflakeID(role.id),
            server_id=SnowflakeID(sid),
            name=role.name,
            color=getattr(role, "color", None),
            position=getattr(role, "position", 0),
            permissions=getattr(role, "permissions", {}),
            hoist=getattr(role, "hoist", False),
            mentionable=getattr(role, "mentionable", False),
            is_default=getattr(role, "is_default", False),
        )
    except HTTPException:
        raise
    except Exception as e:
        exc_name = type(e).__name__
        if "NotFound" in exc_name:
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": 404, "message": "Role not found"}},
            )
        elif "Permission" in exc_name or "Default" in exc_name:
            raise HTTPException(
                status_code=403, detail={"error": {"code": 403, "message": str(e)}}
            )

        logger.error(
            f"Failed to update role {role_id} in server {server_id}: {e}", exc_info=True
        )
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )


@router.delete(
    "/{server_id}/roles/{role_id}",
    response_model=SuccessResponse,
    summary="Delete role",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid ID"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {"model": ErrorResponse, "description": "Access denied"},
        404: {"model": ErrorResponse, "description": "Role not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def delete_role(
    server_id: str, role_id: str, current_user: TokenInfo = Depends(get_current_user)
) -> SuccessResponse:
    """
    Delete a role from a server.

    Removes the role from the server and all members. Requires manage roles permission.
    """
    servers_mod = api.get_servers()
    if not servers_mod:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Servers module not available"}},
        )

    try:
        try:
            sid = int(server_id)
            rid = int(role_id)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid ID"}},
            )

        servers_mod.require_permission(current_user.user_id, sid, "roles.manage")
        servers_mod.delete_role(current_user.user_id, rid)
        return SuccessResponse(success=True)
    except HTTPException:
        raise
    except Exception as e:
        exc_name = type(e).__name__
        if "NotFound" in exc_name:
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": 404, "message": "Role not found"}},
            )
        elif "Permission" in exc_name or "Default" in exc_name:
            raise HTTPException(
                status_code=403, detail={"error": {"code": 403, "message": str(e)}}
            )

        logger.error(
            f"Failed to delete role {role_id} in server {server_id}: {e}", exc_info=True
        )
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )


# ==================== Ban Management ====================


@router.get(
    "/{server_id}/bans",
    response_model=List[BanResponse],
    summary="List server bans",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid server ID"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {"model": ErrorResponse, "description": "Access denied"},
        404: {"model": ErrorResponse, "description": "Server not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_server_bans(
    server_id: str, current_user: TokenInfo = Depends(get_current_user)
) -> List[BanResponse]:
    """
    Get all bans in a server.

    Returns a list of users who are banned from the server. Requires ban members permission.
    """
    servers_mod = api.get_servers()
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

        bans = servers_mod.get_bans(current_user.user_id, sid)
        return [
            BanResponse(
                user_id=SnowflakeID(b.user_id),
                reason=getattr(b, "reason", None),
                banned_by=SnowflakeID(b.banned_by) if hasattr(b, "banned_by") else None,
                banned_at=getattr(b, "banned_at", None),
            )
            for b in (bans or [])
        ]
    except HTTPException:
        raise
    except Exception as e:
        exc_name = type(e).__name__
        if "NotFound" in exc_name:
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": 404, "message": "Server not found"}},
            )
        elif "Permission" in exc_name:
            raise HTTPException(
                status_code=403, detail={"error": {"code": 403, "message": str(e)}}
            )

        logger.error(f"Failed to get bans for server {server_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )


@router.put(
    "/{server_id}/bans/{user_id}",
    response_model=SuccessResponse,
    summary="Ban user",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid ID"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {"model": ErrorResponse, "description": "Access denied"},
        404: {"model": ErrorResponse, "description": "Server or user not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def ban_member(
    server_id: str,
    user_id: str,
    body: Optional[BanCreateRequest] = None,
    current_user: TokenInfo = Depends(get_current_user),
) -> SuccessResponse:
    """
    Ban a user from a server.

    Prevents a user from joining or seeing the server. Requires ban members permission.
    """
    servers_mod = api.get_servers()
    if not servers_mod:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Servers module not available"}},
        )

    try:
        try:
            sid = int(server_id)
            uid = int(user_id)
        except (ValueError, TypeError):
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid ID"}},
            )

        reason = body.reason if body else None
        delete_message_days = body.delete_message_days if body else 0

        servers_mod.ban_member(
            current_user.user_id,
            sid,
            uid,
            reason=reason,
            delete_message_days=delete_message_days,
        )
        return SuccessResponse(success=True)
    except HTTPException:
        raise
    except Exception as e:
        exc_name = type(e).__name__
        if "NotFound" in exc_name:
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": 404, "message": "Server or user not found"}},
            )
        elif "Permission" in exc_name:
            raise HTTPException(
                status_code=403, detail={"error": {"code": 403, "message": str(e)}}
            )

        logger.error(
            f"Failed to ban user {user_id} from server {server_id}: {e}", exc_info=True
        )
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )


@router.delete(
    "/{server_id}/bans/{user_id}",
    response_model=SuccessResponse,
    summary="Unban user",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid ID"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {"model": ErrorResponse, "description": "Access denied"},
        404: {"model": ErrorResponse, "description": "Server or ban not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def unban_member(
    server_id: str, user_id: str, current_user: TokenInfo = Depends(get_current_user)
) -> SuccessResponse:
    """
    Unban a user from a server.

    Allows a previously banned user to join the server again. Requires ban members permission.
    """
    servers_mod = api.get_servers()
    if not servers_mod:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Servers module not available"}},
        )

    try:
        try:
            sid = int(server_id)
            uid = int(user_id)
        except (ValueError, TypeError):
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid ID"}},
            )

        servers_mod.unban_member(current_user.user_id, sid, uid)
        return SuccessResponse(success=True)
    except HTTPException:
        raise
    except Exception as e:
        exc_name = type(e).__name__
        if "NotFound" in exc_name:
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": 404, "message": "Ban not found"}},
            )
        elif "Permission" in exc_name:
            raise HTTPException(
                status_code=403, detail={"error": {"code": 403, "message": str(e)}}
            )

        logger.error(
            f"Failed to unban user {user_id} from server {server_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )


# ==================== Misc ====================


@router.post(
    "/{server_id}/leave",
    response_model=SuccessResponse,
    summary="Leave server",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid server ID"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {"model": ErrorResponse, "description": "Cannot leave as owner"},
        404: {"model": ErrorResponse, "description": "Server not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def leave_server(
    server_id: str, current_user: TokenInfo = Depends(get_current_user)
) -> SuccessResponse:
    """
    Leave a server.

    Removes the current user from the server. Owners cannot leave their own server.
    """
    servers_mod = api.get_servers()
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

        servers_mod.leave_server(current_user.user_id, sid)
        # Invalidate user's server list cache
        invalidate_user_servers(current_user.user_id)
        return SuccessResponse(success=True)
    except HTTPException:
        raise
    except Exception as e:
        exc_name = type(e).__name__
        if "NotFound" in exc_name:
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": 404, "message": "Server not found"}},
            )
        elif "Owner" in exc_name:
            raise HTTPException(
                status_code=403,
                detail={
                    "error": {
                        "code": 403,
                        "message": "Owners cannot leave their own server",
                    }
                },
            )

        logger.error(f"Failed to leave server {server_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )


@router.get(
    "/{server_id}/permissions",
    response_model=Dict[str, bool],
    summary="Get my permissions",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid server ID"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        404: {"model": ErrorResponse, "description": "Server not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_my_permissions(
    server_id: str, current_user: TokenInfo = Depends(get_current_user)
) -> Dict[str, bool]:
    """
    Get current user's permissions in a server.
    """
    servers_mod = api.get_servers()
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

        return servers_mod.get_permissions(current_user.user_id, sid)
    except Exception as e:
        exc_name = type(e).__name__
        if "NotFound" in exc_name:
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": 404, "message": "Server not found"}},
            )

        logger.error(
            f"Failed to get permissions for server {server_id}: {e}", exc_info=True
        )
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )


# ==================== Audit Log ====================


@router.get(
    "/{server_id}/audit-logs",
    response_model=List[AuditLogEntryResponse],
    summary="Get audit logs",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid server ID"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {"model": ErrorResponse, "description": "Access denied"},
        404: {"model": ErrorResponse, "description": "Server not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_audit_log(
    server_id: str, limit: int = 50, current_user: TokenInfo = Depends(get_current_user)
) -> List[AuditLogEntryResponse]:
    """
    Get audit log entries for a server.

    Returns a list of administrative actions taken in the server. Requires view audit log permission.
    """
    servers_mod = api.get_servers()
    auth_mod = api.get_auth()

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

        entries = servers_mod.get_audit_log(current_user.user_id, sid, limit=limit)
        if not entries:
            return []

        # Batch fetch user info
        user_ids = set()
        for e in entries:
            user_ids.add(e.user_id)
            if e.target_type == "member" and e.target_id:
                user_ids.add(int(e.target_id))

        users_map = {}
        if auth_mod:
            users_dict = auth_mod.get_users_bulk(list(user_ids))
            for uid, u in users_dict.items():
                from ..users import _user_to_public_response

                users_map[int(uid)] = _user_to_public_response(u)

        # Cache for roles and channels to avoid repeated lookups
        role_map = {}
        channel_map = {}

        result = []
        for e in entries:
            target_name = None
            if e.target_type == "member" and e.target_id:
                target_user = users_map.get(int(e.target_id))
                target_name = (
                    target_user.username if target_user else f"User {e.target_id}"
                )
            elif e.target_type == "role" and e.target_id:
                rid = int(e.target_id)
                if rid not in role_map:
                    try:
                        role = servers_mod.get_role(rid, current_user.user_id)
                        role_map[rid] = role.name if role else None
                    except Exception:
                        role_map[rid] = None
                target_name = role_map[rid]
            elif e.target_type == "channel" and e.target_id:
                cid = int(e.target_id)
                if cid not in channel_map:
                    try:
                        channel = servers_mod.get_channel(cid, current_user.user_id)
                        channel_map[cid] = channel.name if channel else None
                    except Exception:
                        channel_map[cid] = None
                target_name = channel_map[cid]

            result.append(
                AuditLogEntryResponse(
                    id=SnowflakeID(e.id),
                    server_id=SnowflakeID(e.server_id),
                    user_id=SnowflakeID(e.user_id),
                    user=users_map.get(int(e.user_id)),
                    action=e.action_type.value
                    if hasattr(e.action_type, "value")
                    else str(e.action_type),
                    target_type=getattr(e, "target_type", None),
                    target_id=SnowflakeID(e.target_id)
                    if getattr(e, "target_id", None)
                    else None,
                    target_name=target_name,
                    changes=getattr(e, "changes", None),
                    reason=getattr(e, "reason", None),
                    created_at=getattr(e, "created_at", None),
                )
            )
        return result
    except HTTPException:
        raise
    except Exception as e:
        exc_name = type(e).__name__
        if "NotFound" in exc_name:
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": 404, "message": "Server not found"}},
            )
        elif "Permission" in exc_name:
            raise HTTPException(
                status_code=403, detail={"error": {"code": 403, "message": str(e)}}
            )

        logger.error(
            f"Failed to get audit log for server {server_id}: {e}", exc_info=True
        )
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )


@router.get(
    "/{server_id}/automod/rules",
    response_model=List[AutomodRuleResponse],
    summary="Get server automod rules",
)
async def get_server_automod_rules(
    server_id: str, current_user: TokenInfo = Depends(get_current_user)
) -> List[AutomodRuleResponse]:
    """Get all automod rules for a server."""
    servers_mod = api.get_servers()
    if not servers_mod:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Servers module not available"}},
        )
    from src.core import automod

    try:
        sid = int(server_id)
        servers_mod.require_permission(current_user.user_id, sid, "server.automod")

        rules = automod.get_server_rules(sid)
        return [_automod_rule_to_response(r) for r in rules]
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get automod rules for server {server_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "/{server_id}/automod/rules",
    response_model=AutomodRuleResponse,
    summary="Create server automod rule",
)
async def create_server_automod_rule(
    server_id: str,
    body: AutomodRuleCreateRequest,
    current_user: TokenInfo = Depends(get_current_user),
) -> AutomodRuleResponse:
    """Create a new automod rule for a server."""
    servers_mod = api.get_servers()
    if not servers_mod:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Servers module not available"}},
        )
    from src.core import automod
    from src.core.automod.models import RuleType

    try:
        sid = int(server_id)
        servers_mod.require_permission(current_user.user_id, sid, "server.automod")

        try:
            rule_type = RuleType(body.rule_type)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid rule type")

        applied_roles = (
            [int(r) for r in body.applied_roles]
            if body.applied_roles is not None
            else None
        )
        exempt_roles = (
            [int(r) for r in body.exempt_roles]
            if body.exempt_roles is not None
            else None
        )
        exempt_channels = (
            [int(c) for c in body.exempt_channels]
            if body.exempt_channels is not None
            else None
        )

        rule = automod.create_rule(
            user_id=current_user.user_id,
            server_id=sid,
            name=body.name,
            rule_type=rule_type,
            rule_config=body.config,
            actions=[a.model_dump() for a in body.actions],
            applied_roles=applied_roles,
            exempt_roles=exempt_roles,
            exempt_channels=exempt_channels,
            priority=body.priority or 0,
            check_all=bool(body.check_all),
        )

        if body.enabled is False:
            automod.set_rule_enabled(current_user.user_id, rule.id, False)
            rule = automod.get_rule(rule.id)

        return _automod_rule_to_response(rule)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to create automod rule for server {server_id}: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@router.patch(
    "/{server_id}/automod/rules/{rule_id}",
    response_model=AutomodRuleResponse,
    summary="Update server automod rule",
)
async def update_server_automod_rule(
    server_id: str,
    rule_id: str,
    body: AutomodRuleUpdateRequest,
    current_user: TokenInfo = Depends(get_current_user),
) -> AutomodRuleResponse:
    """Update an existing automod rule."""
    servers_mod = api.get_servers()
    if not servers_mod:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Servers module not available"}},
        )
    from src.core import automod

    try:
        sid = int(server_id)
        rid = int(rule_id)
        servers_mod.require_permission(current_user.user_id, sid, "server.automod")

        rule = automod.get_rule(rid)
        if not rule or int(rule.server_id) != sid:
            raise HTTPException(status_code=404, detail="Rule not found in this server")

        update_kwargs: Dict[str, Any] = {}
        if body.name is not None:
            update_kwargs["name"] = body.name
        if body.config is not None:
            update_kwargs["rule_config"] = body.config
        if body.actions is not None:
            update_kwargs["actions"] = [a.model_dump() for a in body.actions]
        if body.exempt_roles is not None:
            update_kwargs["exempt_roles"] = [int(r) for r in body.exempt_roles]
        if body.applied_roles is not None:
            update_kwargs["applied_roles"] = [int(r) for r in body.applied_roles]
        if body.exempt_channels is not None:
            update_kwargs["exempt_channels"] = [int(c) for c in body.exempt_channels]
        if body.priority is not None:
            update_kwargs["priority"] = body.priority
        if body.check_all is not None:
            update_kwargs["check_all"] = body.check_all

        if update_kwargs:
            automod.update_rule(
                user_id=current_user.user_id, rule_id=rid, **update_kwargs
            )

        if body.enabled is not None:
            automod.set_rule_enabled(current_user.user_id, rid, body.enabled)

        rule = automod.get_rule(rid)
        return _automod_rule_to_response(rule)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update automod rule {rule_id}: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@router.delete(
    "/{server_id}/automod/rules/{rule_id}",
    response_model=SuccessResponse,
    summary="Delete server automod rule",
)
async def delete_server_automod_rule(
    server_id: str, rule_id: str, current_user: TokenInfo = Depends(get_current_user)
) -> SuccessResponse:
    """Delete an automod rule."""
    servers_mod = api.get_servers()
    if not servers_mod:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Servers module not available"}},
        )
    from src.core import automod

    try:
        sid = int(server_id)
        rid = int(rule_id)
        servers_mod.require_permission(current_user.user_id, sid, "server.automod")

        rule = automod.get_rule(rid)
        if not rule or int(rule.server_id) != sid:
            raise HTTPException(status_code=404, detail="Rule not found in this server")

        if not automod.delete_rule(user_id=current_user.user_id, rule_id=rid):
            raise HTTPException(status_code=500, detail="Failed to delete rule")

        return SuccessResponse(success=True)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete automod rule {rule_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get(
    "/{server_id}/automod/violations",
    response_model=List[AutomodViolationResponse],
    summary="Get server automod violations",
)
async def get_server_automod_violations(
    server_id: str,
    user_id: Optional[str] = None,
    limit: int = 50,
    before: Optional[str] = None,
    current_user: TokenInfo = Depends(get_current_user),
) -> List[AutomodViolationResponse]:
    """Get recent automod violations for a server."""
    servers_mod = api.get_servers()
    if not servers_mod:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Servers module not available"}},
        )
    from src.core import automod

    try:
        sid = int(server_id)
        servers_mod.require_permission(current_user.user_id, sid, "server.automod")

        target_user_id = int(user_id) if user_id else None
        before_id = int(before) if before else None

        violations = automod.get_violations(
            sid, user_id=target_user_id, limit=limit, before_id=before_id
        )

        return [
            AutomodViolationResponse(
                id=SnowflakeID(v.id),
                user_id=SnowflakeID(v.user_id),
                channel_id=SnowflakeID(v.channel_id),
                rule_id=SnowflakeID(v.rule_id),
                rule_type=v.rule_type.value
                if hasattr(v.rule_type, "value")
                else str(v.rule_type),
                matched_content=v.matched_content,
                severity=v.severity.value
                if hasattr(v.severity, "value")
                else str(v.severity),
                actions_taken=[
                    a.value if hasattr(a, "value") else str(a) for a in v.actions_taken
                ],
                created_at=v.created_at,
                metadata=v.metadata or {},
            )
            for v in violations
        ]
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get automod violations for server {server_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== Webhook Management ====================


@router.get(
    "/{server_id}/webhooks",
    response_model=List[WebhookResponse],
    summary="List server webhooks",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid server ID"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {"model": ErrorResponse, "description": "Access denied"},
        404: {"model": ErrorResponse, "description": "Server not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_server_webhooks(
    server_id: str, current_user: TokenInfo = Depends(get_current_user)
) -> List[WebhookResponse]:
    """
    Get all webhooks in a server.

    Returns a list of webhooks created in the server. Requires manage webhooks permission.
    """
    webhooks_mod = api.get_webhooks()
    if not webhooks_mod:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Webhooks module not available"}},
        )

    try:
        try:
            sid = int(server_id)
        except (ValueError, TypeError):
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid server ID"}},
            )

        webhooks = webhooks_mod.get_server_webhooks(current_user.user_id, sid)
        return [
            WebhookResponse(
                id=SnowflakeID(w.id),
                channel_id=SnowflakeID(w.channel_id),
                server_id=SnowflakeID(w.server_id),
                creator_id=SnowflakeID(w.creator_id)
                if getattr(w, "creator_id", 0)
                else None,
                name=w.name,
                avatar_url=w.avatar_url,
                created_at=w.created_at,
            )
            for w in (webhooks or [])
        ]
    except HTTPException:
        raise
    except Exception as e:
        exc_name = type(e).__name__
        if "NotFound" in exc_name:
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": 404, "message": "Server not found"}},
            )
        elif "Permission" in exc_name:
            raise HTTPException(
                status_code=403, detail={"error": {"code": 403, "message": str(e)}}
            )

        logger.error(
            f"Failed to get webhooks for server {server_id}: {e}", exc_info=True
        )
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )


@router.post(
    "/{server_id}/icon",
    response_model=ServerResponse,
    summary="Upload server icon",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid server ID or data"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {"model": ErrorResponse, "description": "Access denied"},
        404: {"model": ErrorResponse, "description": "Server not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def upload_server_icon(
    server_id: str,
    file: UploadFile = File(...),
    current_user: TokenInfo = Depends(get_current_user),
) -> ServerResponse:
    """
    Upload a server icon.

    Uploads and sets a new icon for the server. Requires manage server permission.
    """
    servers_mod = api.get_servers()
    media = api.get_media()

    if not servers_mod:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Servers module not available"}},
        )
    if not media:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Media module not available"}},
        )

    try:
        try:
            sid = int(server_id)
        except (ValueError, TypeError):
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid server ID"}},
            )

        # Check permission
        server = servers_mod.get_server(sid, current_user.user_id)
        if not server:
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": 404, "message": "Server not found"}},
            )

        servers_mod.require_permission(current_user.user_id, sid, "server.manage")

        # Use avatars module for upload (handles resizing and database storage)
        avatars = api.get_avatars()
        if not avatars:
            raise HTTPException(
                status_code=500,
                detail={
                    "error": {"code": 500, "message": "Avatars module not available"}
                },
            )

        content = await file.read()
        result = avatars.upload_server_icon(
            server_id=sid,
            image_data=content,
            content_type=file.content_type,
        )

        # Update server with new icon URL from avatars module
        server = servers_mod.update_server(
            current_user.user_id, sid, icon_url=result["url"]
        )
        return _server_to_response(server)
    except HTTPException:
        raise
    except Exception as e:
        exc_name = type(e).__name__
        if "Size" in exc_name or "Type" in exc_name:
            raise HTTPException(
                status_code=400, detail={"error": {"code": 400, "message": str(e)}}
            )
        elif "Blocked" in exc_name or "Malware" in exc_name:
            raise HTTPException(
                status_code=400, detail={"error": {"code": 400, "message": str(e)}}
            )

        logger.error(
            f"Server icon upload failed for server {server_id}: {e}", exc_info=True
        )
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": "Upload failed"}}
        )
