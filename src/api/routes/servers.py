"""
Server routes - Server/guild management endpoints.
"""

from typing import List, Optional
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
)
from src.api.schemas.common import SnowflakeID, ErrorResponse, SuccessResponse
from src.core.servers.models import ChannelType
from src.core.database import (
    invalidate_user_servers,
    invalidate_server_channels,
    invalidate_server,
    cached,
)

import utils.config as config
import utils.logger as logger

router = APIRouter(tags=["Servers"])


def _server_to_response(server) -> ServerResponse:
    """Convert server object to response model."""
    default_channel_id = getattr(server, "default_channel_id", None)
    # Use getattr to correctly pick up the icon_url property from the Server model
    icon_url = getattr(server, "icon_url", None)
    return ServerResponse(
        id=SnowflakeID(server.id),
        name=server.name,
        description=getattr(server, "description", None),
        icon_url=icon_url,
        owner_id=SnowflakeID(server.owner_id),
        member_count=getattr(server, "member_count", 0),
        default_channel_id=SnowflakeID(default_channel_id)
        if default_channel_id
        else None,
        verification_level=getattr(server, "verification_level", 0),
        default_message_notifications=getattr(server, "default_message_notifications", 0),
        created_at=server.created_at,
    )


def _channel_to_response(channel) -> ChannelResponse:
    """Convert channel object to response model."""
    channel_type = getattr(channel, "channel_type", None)
    if channel_type is not None and hasattr(channel_type, "value"):
        channel_type = channel_type.value

    return ChannelResponse(
        id=SnowflakeID(channel.id),
        server_id=SnowflakeID(channel.server_id),
        name=channel.name,
        channel_type=channel_type or "text",
        topic=getattr(channel, "topic", None),
        position=getattr(channel, "position", 0),
        category_id=SnowflakeID(channel.category_id)
        if getattr(channel, "category_id", None)
        else None,
        nsfw=getattr(channel, "nsfw", False),
        slowmode_seconds=getattr(channel, "slowmode_seconds", 0),
        created_at=channel.created_at,
    )


def _server_to_dict(server) -> dict:
    """Convert server object to JSON-serializable dict for caching."""
    default_channel_id = getattr(server, "default_channel_id", None)
    return {
        "id": server.id,
        "name": server.name,
        "description": getattr(server, "description", None),
        "icon_url": getattr(server, "icon_url", None),
        "owner_id": server.owner_id,
        "member_count": getattr(server, "member_count", 0),
        "default_channel_id": default_channel_id,
        "created_at": server.created_at,
    }


def _channel_to_dict(channel) -> dict:
    """Convert channel object to JSON-serializable dict for caching."""
    channel_type = getattr(channel, "channel_type", None)
    if channel_type is not None and hasattr(channel_type, "value"):
        channel_type = channel_type.value
    return {
        "id": channel.id,
        "server_id": channel.server_id,
        "name": channel.name,
        "channel_type": channel_type or "text",
        "topic": getattr(channel, "topic", None),
        "position": getattr(channel, "position", 0),
        "category_id": getattr(channel, "category_id", None),
        "nsfw": getattr(channel, "nsfw", False),
        "slowmode_seconds": getattr(channel, "slowmode_seconds", 0),
        "created_at": channel.created_at,
    }


@router.get(
    "",
    response_model=List[ServerResponse],
    summary="List joined servers",
    responses={
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_servers(
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
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
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
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
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
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
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
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
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
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
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
async def get_server_channels(
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
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
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
async def get_server_members(
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
        curr_user_id = current_user.user_id if hasattr(current_user, "user_id") else current_user.get("user_id")

        members = servers_mod.get_members(curr_user_id, sid)
        if not members:
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
                presence_map = presence.get_visible_presences_bulk(curr_user_id, user_ids)
            except Exception as e:
                logger.warning(f"Failed to get bulk presence for server {sid}: {e}")

        result = []
        for m in members:
            # Handle both objects and dicts (from cache)
            user_id = getattr(m, "user_id", None) or m.get("user_id")
            nickname = getattr(m, "nickname", None) or m.get("nickname")
            joined_at = getattr(m, "joined_at", None) or m.get("joined_at")
            roles = getattr(m, "roles", []) or m.get("roles", [])
            
            user = users_map.get(user_id)
            
            # Presence info
            pres = presence_map.get(user_id)
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
                    user_id=SnowflakeID(user_id),
                    username=user.username if user else f"User {user_id}",
                    nickname=nickname,
                    avatar_url=getattr(user, "avatar_url", None),
                    joined_at=joined_at,
                    roles=[SnowflakeID(r) for r in (roles or [])],
                    presence=presence_data,
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

        servers_mod.kick_member(current_user.user_id, sid, mid)
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

        servers_mod.assign_role(current_user.user_id, sid, mid, rid)
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

        servers_mod.remove_role(current_user.user_id, sid, mid, rid)
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

    try:
        # Build kwargs, only including supported parameters
        kwargs = {
            "user_id": current_user.user_id,
            "server_id": sid,
            "name": body.name,
        }
        if body.type:
            # Convert string to ChannelType enum
            type_str = body.type.lower()
            try:
                kwargs["channel_type"] = ChannelType(type_str)
            except ValueError:
                kwargs["channel_type"] = ChannelType.TEXT
        if body.topic:
            kwargs["topic"] = body.topic
        if body.category_id:
            kwargs["category_id"] = int(body.category_id)
        if body.nsfw:
            kwargs["nsfw"] = body.nsfw

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
            int(server_id)
            rid = int(role_id)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid ID"}},
            )

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
        return [
            AuditLogEntryResponse(
                id=SnowflakeID(e.id),
                server_id=SnowflakeID(e.server_id),
                user_id=SnowflakeID(e.user_id),
                action=e.action_type.value
                if hasattr(e.action_type, "value")
                else str(e.action_type),
                target_type=getattr(e, "target_type", None),
                target_id=SnowflakeID(e.target_id)
                if getattr(e, "target_id", None)
                else None,
                changes=getattr(e, "changes", None),
                reason=getattr(e, "reason", None),
                created_at=getattr(e, "created_at", None),
            )
            for e in (entries or [])
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
            f"Failed to get audit log for server {server_id}: {e}", exc_info=True
        )
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )


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


# ==================== Server Icon Upload ====================

# Default icon size limit (2MB)
DEFAULT_ICON_SIZE_LIMIT = 2 * 1024 * 1024


def _get_icon_size_limit() -> int:
    """Get the icon upload size limit from config."""
    try:
        media_config = config.get("media", {})
        size_limits = media_config.get("size_limits", {})
        return size_limits.get("icon", DEFAULT_ICON_SIZE_LIMIT)
    except Exception:
        return DEFAULT_ICON_SIZE_LIMIT


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
                detail={"error": {"code": 500, "message": "Avatars module not available"}}
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
