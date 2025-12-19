"""
Server routes - Server/guild management endpoints.
"""

import os
import uuid
from pathlib import Path
from typing import List, Optional
from fastapi import APIRouter, HTTPException, Depends, File, UploadFile

import src.api as api
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.servers import (
    ServerCreateRequest,
    ServerUpdateRequest,
    ServerResponse,
    ChannelResponse,
)
from src.api.schemas.common import SnowflakeID
from src.core.servers.models import ChannelType
from src.core.database import cached

import utils.config as config

router = APIRouter()


def _server_to_response(server) -> ServerResponse:
    """Convert server object to response model."""
    default_channel_id = getattr(server, "default_channel_id", None)
    return ServerResponse(
        id=SnowflakeID(server.id),
        name=server.name,
        description=getattr(server, "description", None),
        icon_url=getattr(server, "icon_url", None),
        owner_id=SnowflakeID(server.owner_id),
        member_count=getattr(server, "member_count", 0),
        default_channel_id=SnowflakeID(default_channel_id) if default_channel_id else None,
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
        category_id=SnowflakeID(channel.category_id) if getattr(channel, "category_id", None) else None,
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


def _get_servers_cached(user_id: int):
    """Get user's servers as JSON-serializable dicts for caching."""
    servers_mod = api.get_servers()
    if not servers_mod:
        return None
    servers = servers_mod.get_servers(user_id)
    return [_server_to_dict(s) for s in servers] if servers else []


def _get_channels_cached(user_id: int, server_id: int):
    """Get server channels as JSON-serializable dicts for caching."""
    servers_mod = api.get_servers()
    if not servers_mod:
        return None
    channels = servers_mod.get_channels(user_id, server_id)
    return [_channel_to_dict(c) for c in channels] if channels else []


# Apply caching (30s TTL for server lists, 30s for channels)
_get_servers_cached = cached(ttl=30, prefix="servers")(_get_servers_cached)
_get_channels_cached = cached(ttl=30, prefix="channels")(_get_channels_cached)


@router.get("", response_model=List[ServerResponse])
async def get_servers(current_user: TokenInfo = Depends(get_current_user)):
    """
    Get all servers the user is a member of.
    
    Returns a list of servers the authenticated user belongs to.
    """
    servers_mod = api.get_servers()
    if not servers_mod:
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Servers module not available"}})

    try:
        servers = _get_servers_cached(current_user.user_id)
        if servers is None:
            raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Failed to fetch servers"}})
        # Cached data is already dict format, convert to response
        return [ServerResponse(
            id=SnowflakeID(s["id"]),
            name=s["name"],
            description=s.get("description"),
            icon_url=s.get("icon_url"),
            owner_id=SnowflakeID(s["owner_id"]),
            member_count=s.get("member_count", 0),
            default_channel_id=SnowflakeID(s["default_channel_id"]) if s.get("default_channel_id") else None,
            created_at=s["created_at"],
        ) for s in servers]
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": str(e)}})


@router.post("", response_model=ServerResponse)
async def create_server(
    body: ServerCreateRequest,
    current_user: TokenInfo = Depends(get_current_user)
):
    """
    Create a new server.
    
    Creates a server with the authenticated user as owner.
    """
    servers_mod = api.get_servers()
    if not servers_mod:
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Servers module not available"}})

    try:
        server = servers_mod.create_server(
            owner_id=current_user.user_id,
            name=body.name,
            description=body.description
        )
        return _server_to_response(server)
    except Exception as e:
        exc_name = type(e).__name__
        if "Limit" in exc_name:
            raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": str(e)}})
        raise


@router.get("/{server_id}", response_model=ServerResponse)
async def get_server(server_id: str, current_user: TokenInfo = Depends(get_current_user)):
    """
    Get server by ID.
    
    Returns server information if the user is a member.
    """
    servers_mod = api.get_servers()
    if not servers_mod:
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Servers module not available"}})

    try:
        sid = int(server_id)
    except ValueError:
        raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": "Invalid server ID"}})

    try:
        server = servers_mod.get_server(sid, current_user.user_id)
        if not server:
            raise HTTPException(status_code=404, detail={"error": {"code": 404, "message": "Server not found"}})
        return _server_to_response(server)
    except HTTPException:
        raise
    except Exception as e:
        exc_name = type(e).__name__
        if "NotFound" in exc_name:
            raise HTTPException(status_code=404, detail={"error": {"code": 404, "message": "Server not found"}})
        elif "Access" in exc_name or "Permission" in exc_name:
            raise HTTPException(status_code=403, detail={"error": {"code": 403, "message": "Access denied"}})
        raise


@router.patch("/{server_id}", response_model=ServerResponse)
async def update_server(
    server_id: str,
    body: ServerUpdateRequest,
    current_user: TokenInfo = Depends(get_current_user)
):
    """
    Update server settings.
    
    Updates server information. Requires manage server permission.
    """
    servers_mod = api.get_servers()
    if not servers_mod:
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Servers module not available"}})

    try:
        sid = int(server_id)
    except ValueError:
        raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": "Invalid server ID"}})

    try:
        update_data = body.model_dump(exclude_unset=True)
        server = servers_mod.update_server(current_user.user_id, sid, **update_data)
        return _server_to_response(server)
    except Exception as e:
        exc_name = type(e).__name__
        if "NotFound" in exc_name:
            raise HTTPException(status_code=404, detail={"error": {"code": 404, "message": "Server not found"}})
        elif "Permission" in exc_name:
            raise HTTPException(status_code=403, detail={"error": {"code": 403, "message": str(e)}})
        raise


@router.delete("/{server_id}")
async def delete_server(server_id: str, current_user: TokenInfo = Depends(get_current_user)):
    """
    Delete a server.
    
    Permanently deletes the server. Only the owner can delete.
    """
    servers_mod = api.get_servers()
    if not servers_mod:
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Servers module not available"}})

    try:
        sid = int(server_id)
    except ValueError:
        raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": "Invalid server ID"}})

    try:
        servers_mod.delete_server(current_user.user_id, sid)
        return {"success": True}
    except Exception as e:
        exc_name = type(e).__name__
        if "NotFound" in exc_name:
            raise HTTPException(status_code=404, detail={"error": {"code": 404, "message": "Server not found"}})
        elif "Permission" in exc_name or "Owner" in exc_name:
            raise HTTPException(status_code=403, detail={"error": {"code": 403, "message": str(e)}})
        raise


@router.get("/{server_id}/channels", response_model=List[ChannelResponse])
async def get_server_channels(server_id: str, current_user: TokenInfo = Depends(get_current_user)):
    """
    Get all channels in a server.
    
    Returns channels the user has access to view.
    """
    servers_mod = api.get_servers()
    if not servers_mod:
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Servers module not available"}})

    try:
        sid = int(server_id)
    except ValueError:
        raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": "Invalid server ID"}})

    try:
        channels = _get_channels_cached(current_user.user_id, sid)
        if channels is None:
            raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Failed to fetch channels"}})
        # Cached data is already dict format, convert to response
        return [ChannelResponse(
            id=SnowflakeID(c["id"]),
            server_id=SnowflakeID(c["server_id"]),
            name=c["name"],
            channel_type=c.get("channel_type", "text"),
            topic=c.get("topic"),
            position=c.get("position", 0),
            category_id=SnowflakeID(c["category_id"]) if c.get("category_id") else None,
            nsfw=c.get("nsfw", False),
            slowmode_seconds=c.get("slowmode_seconds", 0),
            created_at=c["created_at"],
        ) for c in channels]
    except HTTPException:
        raise
    except Exception as e:
        exc_name = type(e).__name__
        if "NotFound" in exc_name:
            raise HTTPException(status_code=404, detail={"error": {"code": 404, "message": "Server not found"}})
        elif "Access" in exc_name:
            raise HTTPException(status_code=403, detail={"error": {"code": 403, "message": "Access denied"}})
        raise


@router.get("/{server_id}/members")
async def get_server_members(
    server_id: str,
    limit: int = 100,
    after: Optional[str] = None,
    current_user: TokenInfo = Depends(get_current_user)
):
    """Get server members."""
    servers_mod = api.get_servers()
    auth = api.get_auth()
    presence = api.get_presence()

    if not servers_mod:
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Servers module not available"}})

    try:
        sid = int(server_id)
    except ValueError:
        raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": "Invalid server ID"}})

    try:
        members = servers_mod.get_members(current_user.user_id, sid)
        result = []
        for m in (members or []):
            user_id = getattr(m, "user_id", 0)

            # Get user info
            username = None
            avatar_url = None
            if auth:
                try:
                    user = auth.get_user(user_id)
                    if user:
                        username = user.username
                        avatar_url = getattr(user, "avatar_url", None)
                except Exception:
                    pass

            # Get presence info - default to offline if not found
            presence_data = {"status": "offline"}
            if presence:
                try:
                    pres = presence.get_visible_presence(current_user.user_id, user_id)
                    if pres:
                        status = getattr(pres, "status", None)
                        if status and hasattr(status, "value"):
                            status = status.value
                        presence_data = {"status": str(status) if status else "offline"}
                except Exception as e:
                    logger.warning(f"Failed to get presence for user {user_id}: {e}")

            result.append({
                "user_id": str(user_id),
                "username": username or f"User {user_id}",
                "nickname": getattr(m, "nickname", None),
                "avatar_url": avatar_url,
                "joined_at": getattr(m, "joined_at", None),
                "roles": [str(r) for r in getattr(m, "roles", [])] if hasattr(m, "roles") else [],
                "presence": presence_data
            })
        return result
    except Exception as e:
        exc_name = type(e).__name__
        if "NotFound" in exc_name:
            raise HTTPException(status_code=404, detail={"error": {"code": 404, "message": "Server not found"}})
        elif "Access" in exc_name:
            raise HTTPException(status_code=403, detail={"error": {"code": 403, "message": "Access denied"}})
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": str(e)}})


# ==================== Member Management ====================

@router.delete("/{server_id}/members/{member_id}")
async def kick_member(
    server_id: str,
    member_id: str,
    current_user: TokenInfo = Depends(get_current_user)
):
    """
    Kick a member from a server.
    
    Removes the member from the server. Requires kick members permission.
    """
    servers_mod = api.get_servers()
    if not servers_mod:
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Servers module not available"}})

    try:
        sid = int(server_id)
        mid = int(member_id)
    except ValueError:
        raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": "Invalid ID"}})

    try:
        servers_mod.kick_member(current_user.user_id, sid, mid)
        return {"success": True}
    except Exception as e:
        exc_name = type(e).__name__
        if "NotFound" in exc_name:
            raise HTTPException(status_code=404, detail={"error": {"code": 404, "message": "Member not found"}})
        elif "Permission" in exc_name or "Hierarchy" in exc_name or "Owner" in exc_name:
            raise HTTPException(status_code=403, detail={"error": {"code": 403, "message": str(e)}})
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": str(e)}})


@router.put("/{server_id}/members/{member_id}/roles/{role_id}")
async def assign_role_to_member(
    server_id: str,
    member_id: str,
    role_id: str,
    current_user: TokenInfo = Depends(get_current_user)
):
    """
    Assign a role to a member.
    
    Adds the specified role to the member. Requires manage roles permission.
    """
    servers_mod = api.get_servers()
    if not servers_mod:
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Servers module not available"}})

    try:
        sid = int(server_id)
        mid = int(member_id)
        rid = int(role_id)
    except ValueError:
        raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": "Invalid ID"}})

    try:
        servers_mod.assign_role(current_user.user_id, sid, mid, rid)
        return {"success": True}
    except Exception as e:
        exc_name = type(e).__name__
        if "NotFound" in exc_name:
            raise HTTPException(status_code=404, detail={"error": {"code": 404, "message": "Member or role not found"}})
        elif "Permission" in exc_name or "Hierarchy" in exc_name:
            raise HTTPException(status_code=403, detail={"error": {"code": 403, "message": str(e)}})
        elif "Exists" in exc_name:
            return {"success": True}  # Already has role, treat as success
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": str(e)}})


@router.delete("/{server_id}/members/{member_id}/roles/{role_id}")
async def remove_role_from_member(
    server_id: str,
    member_id: str,
    role_id: str,
    current_user: TokenInfo = Depends(get_current_user)
):
    """
    Remove a role from a member.
    
    Removes the specified role from the member. Requires manage roles permission.
    """
    servers_mod = api.get_servers()
    if not servers_mod:
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Servers module not available"}})

    try:
        sid = int(server_id)
        mid = int(member_id)
        rid = int(role_id)
    except ValueError:
        raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": "Invalid ID"}})

    try:
        servers_mod.remove_role(current_user.user_id, sid, mid, rid)
        return {"success": True}
    except Exception as e:
        exc_name = type(e).__name__
        if "NotFound" in exc_name:
            raise HTTPException(status_code=404, detail={"error": {"code": 404, "message": "Member or role not found"}})
        elif "Permission" in exc_name or "Hierarchy" in exc_name or "Default" in exc_name:
            raise HTTPException(status_code=403, detail={"error": {"code": 403, "message": str(e)}})
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": str(e)}})


# ==================== Channel Management ====================

@router.post("/{server_id}/channels")
async def create_server_channel(
    server_id: str,
    body: dict,
    current_user: TokenInfo = Depends(get_current_user)
):
    """Create a channel in a server."""
    servers_mod = api.get_servers()
    if not servers_mod:
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Servers module not available"}})

    try:
        sid = int(server_id)
    except ValueError:
        raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": "Invalid server ID"}})

    name = body.get("name")
    if not name:
        raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": "Channel name required"}})

    try:
        # Build kwargs, only including supported parameters
        kwargs = {
            "user_id": current_user.user_id,
            "server_id": sid,
            "name": name,
        }
        if body.get("type"):
            # Convert string to ChannelType enum
            type_str = body.get("type", "text").lower()
            try:
                kwargs["channel_type"] = ChannelType(type_str)
            except ValueError:
                kwargs["channel_type"] = ChannelType.TEXT
        if body.get("topic"):
            kwargs["topic"] = body.get("topic")
        if body.get("category_id"):
            kwargs["category_id"] = int(body["category_id"])
        if body.get("nsfw"):
            kwargs["nsfw"] = body.get("nsfw", False)

        channel = servers_mod.create_channel(**kwargs)
        return _channel_to_response(channel)
    except TypeError as e:
        # Handle unexpected keyword arguments by trying simpler call
        if "unexpected keyword argument" in str(e):
            channel = servers_mod.create_channel(
                user_id=current_user.user_id,
                server_id=sid,
                name=name
            )
            return _channel_to_response(channel)
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": str(e)}})
    except Exception as e:
        exc_name = type(e).__name__
        if "NotFound" in exc_name:
            raise HTTPException(status_code=404, detail={"error": {"code": 404, "message": "Server not found"}})
        elif "Permission" in exc_name:
            raise HTTPException(status_code=403, detail={"error": {"code": 403, "message": str(e)}})
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": str(e)}})


@router.get("/{server_id}/invites")
async def get_server_invites(server_id: str, current_user: TokenInfo = Depends(get_current_user)):
    """Get all invites for a server."""
    servers_mod = api.get_servers()
    if not servers_mod:
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Servers module not available"}})

    try:
        sid = int(server_id)
    except ValueError:
        raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": "Invalid server ID"}})

    try:
        invites = servers_mod.get_invites(current_user.user_id, sid)
        return [
            {
                "code": inv.code,
                "server_id": str(inv.server_id),
                "channel_id": str(inv.channel_id) if hasattr(inv, "channel_id") else None,
                "inviter_id": str(inv.inviter_id) if hasattr(inv, "inviter_id") else None,
                "uses": getattr(inv, "uses", 0),
                "max_uses": getattr(inv, "max_uses", 0),
                "max_age": getattr(inv, "max_age", 86400),
                "temporary": getattr(inv, "temporary", False),
                "created_at": getattr(inv, "created_at", None),
                "expires_at": getattr(inv, "expires_at", None),
            }
            for inv in (invites or [])
        ]
    except Exception as e:
        exc_name = type(e).__name__
        if "NotFound" in exc_name:
            raise HTTPException(status_code=404, detail={"error": {"code": 404, "message": "Server not found"}})
        elif "Permission" in exc_name:
            raise HTTPException(status_code=403, detail={"error": {"code": 403, "message": str(e)}})
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": str(e)}})


# ==================== Role Management ====================

@router.get("/{server_id}/roles")
async def get_server_roles(server_id: str, current_user: TokenInfo = Depends(get_current_user)):
    """Get all roles in a server."""
    servers_mod = api.get_servers()
    if not servers_mod:
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Servers module not available"}})

    try:
        sid = int(server_id)
    except ValueError:
        raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": "Invalid server ID"}})

    try:
        roles = servers_mod.get_roles(current_user.user_id, sid)
        return [
            {
                "id": str(r.id),
                "server_id": str(sid),
                "name": r.name,
                "color": getattr(r, "color", None),
                "position": getattr(r, "position", 0),
                "permissions": getattr(r, "permissions", {}),
                "hoist": getattr(r, "hoist", False),
                "mentionable": getattr(r, "mentionable", False),
                "is_default": getattr(r, "is_default", False),
            }
            for r in (roles or [])
        ]
    except Exception as e:
        exc_name = type(e).__name__
        if "NotFound" in exc_name:
            raise HTTPException(status_code=404, detail={"error": {"code": 404, "message": "Server not found"}})
        elif "Access" in exc_name:
            raise HTTPException(status_code=403, detail={"error": {"code": 403, "message": "Access denied"}})
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": str(e)}})


@router.post("/{server_id}/roles")
async def create_role(server_id: str, body: dict, current_user: TokenInfo = Depends(get_current_user)):
    """Create a new role in a server."""
    servers_mod = api.get_servers()
    if not servers_mod:
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Servers module not available"}})

    try:
        sid = int(server_id)
    except ValueError:
        raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": "Invalid server ID"}})

    name = body.get("name", "New Role")
    color = body.get("color")
    permissions = body.get("permissions", {})
    hoist = body.get("hoist", False)
    mentionable = body.get("mentionable", False)

    try:
        role = servers_mod.create_role(
            user_id=current_user.user_id,
            server_id=sid,
            name=name,
            color=color,
            permissions=permissions,
            hoist=hoist,
            mentionable=mentionable
        )
        return {
            "id": str(role.id),
            "server_id": str(sid),
            "name": role.name,
            "color": getattr(role, "color", None),
            "position": getattr(role, "position", 0),
            "permissions": getattr(role, "permissions", {}),
            "hoist": getattr(role, "hoist", False),
            "mentionable": getattr(role, "mentionable", False),
        }
    except Exception as e:
        exc_name = type(e).__name__
        if "NotFound" in exc_name:
            raise HTTPException(status_code=404, detail={"error": {"code": 404, "message": "Server not found"}})
        elif "Permission" in exc_name:
            raise HTTPException(status_code=403, detail={"error": {"code": 403, "message": str(e)}})
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": str(e)}})


@router.patch("/{server_id}/roles/{role_id}")
async def update_role(server_id: str, role_id: str, body: dict, current_user: TokenInfo = Depends(get_current_user)):
    """Update a role in a server."""
    servers_mod = api.get_servers()
    if not servers_mod:
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Servers module not available"}})

    try:
        sid = int(server_id)
        rid = int(role_id)
    except ValueError:
        raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": "Invalid ID"}})

    try:
        role = servers_mod.update_role(current_user.user_id, sid, rid, **body)
        return {
            "id": str(role.id),
            "server_id": str(sid),
            "name": role.name,
            "color": getattr(role, "color", None),
            "position": getattr(role, "position", 0),
            "permissions": getattr(role, "permissions", {}),
            "hoist": getattr(role, "hoist", False),
            "mentionable": getattr(role, "mentionable", False),
        }
    except Exception as e:
        exc_name = type(e).__name__
        if "NotFound" in exc_name:
            raise HTTPException(status_code=404, detail={"error": {"code": 404, "message": "Role not found"}})
        elif "Permission" in exc_name or "Default" in exc_name:
            raise HTTPException(status_code=403, detail={"error": {"code": 403, "message": str(e)}})
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": str(e)}})


@router.delete("/{server_id}/roles/{role_id}")
async def delete_role(server_id: str, role_id: str, current_user: TokenInfo = Depends(get_current_user)):
    """Delete a role from a server."""
    servers_mod = api.get_servers()
    if not servers_mod:
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Servers module not available"}})

    try:
        sid = int(server_id)
        rid = int(role_id)
    except ValueError:
        raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": "Invalid ID"}})

    try:
        servers_mod.delete_role(current_user.user_id, sid, rid)
        return {"success": True}
    except Exception as e:
        exc_name = type(e).__name__
        if "NotFound" in exc_name:
            raise HTTPException(status_code=404, detail={"error": {"code": 404, "message": "Role not found"}})
        elif "Permission" in exc_name or "Default" in exc_name:
            raise HTTPException(status_code=403, detail={"error": {"code": 403, "message": str(e)}})
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": str(e)}})


# ==================== Ban Management ====================

@router.get("/{server_id}/bans")
async def get_server_bans(server_id: str, current_user: TokenInfo = Depends(get_current_user)):
    """Get all bans in a server."""
    servers_mod = api.get_servers()
    if not servers_mod:
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Servers module not available"}})

    try:
        sid = int(server_id)
    except ValueError:
        raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": "Invalid server ID"}})

    try:
        bans = servers_mod.get_bans(current_user.user_id, sid)
        return [
            {
                "user_id": str(b.user_id),
                "reason": getattr(b, "reason", None),
                "banned_by": str(b.banned_by) if hasattr(b, "banned_by") else None,
                "banned_at": getattr(b, "banned_at", None),
            }
            for b in (bans or [])
        ]
    except Exception as e:
        exc_name = type(e).__name__
        if "NotFound" in exc_name:
            raise HTTPException(status_code=404, detail={"error": {"code": 404, "message": "Server not found"}})
        elif "Permission" in exc_name:
            raise HTTPException(status_code=403, detail={"error": {"code": 403, "message": str(e)}})
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": str(e)}})


@router.put("/{server_id}/bans/{user_id}")
async def ban_member(server_id: str, user_id: str, body: Optional[dict] = None, current_user: TokenInfo = Depends(get_current_user)):
    """Ban a user from a server."""
    servers_mod = api.get_servers()
    if not servers_mod:
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Servers module not available"}})

    try:
        sid = int(server_id)
        uid = int(user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": "Invalid ID"}})

    body = body or {}
    reason = body.get("reason")
    delete_message_days = body.get("delete_message_days", 0)

    try:
        servers_mod.ban_member(current_user.user_id, sid, uid, reason=reason, delete_message_days=delete_message_days)
        return {"success": True}
    except Exception as e:
        exc_name = type(e).__name__
        if "NotFound" in exc_name:
            raise HTTPException(status_code=404, detail={"error": {"code": 404, "message": "User not found"}})
        elif "Permission" in exc_name:
            raise HTTPException(status_code=403, detail={"error": {"code": 403, "message": str(e)}})
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": str(e)}})


@router.delete("/{server_id}/bans/{user_id}")
async def unban_member(server_id: str, user_id: str, current_user: TokenInfo = Depends(get_current_user)):
    """Unban a user from a server."""
    servers_mod = api.get_servers()
    if not servers_mod:
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Servers module not available"}})

    try:
        sid = int(server_id)
        uid = int(user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": "Invalid ID"}})

    try:
        servers_mod.unban_member(current_user.user_id, sid, uid)
        return {"success": True}
    except Exception as e:
        exc_name = type(e).__name__
        if "NotFound" in exc_name:
            raise HTTPException(status_code=404, detail={"error": {"code": 404, "message": "Ban not found"}})
        elif "Permission" in exc_name:
            raise HTTPException(status_code=403, detail={"error": {"code": 403, "message": str(e)}})
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": str(e)}})


# ==================== Audit Log ====================

@router.get("/{server_id}/audit-logs")
async def get_audit_log(
    server_id: str,
    limit: int = 50,
    current_user: TokenInfo = Depends(get_current_user)
):
    """Get audit log entries for a server. Requires view audit log permission."""
    servers_mod = api.get_servers()
    if not servers_mod:
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Servers module not available"}})

    try:
        sid = int(server_id)
    except ValueError:
        raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": "Invalid server ID"}})

    try:
        entries = servers_mod.get_audit_log(current_user.user_id, sid, limit=limit)
        return [
            {
                "id": str(e.id),
                "server_id": str(e.server_id),
                "user_id": str(e.user_id),
                "action": e.action.value if hasattr(e.action, "value") else str(e.action),
                "target_type": getattr(e, "target_type", None),
                "target_id": str(e.target_id) if getattr(e, "target_id", None) else None,
                "changes": getattr(e, "changes", None),
                "reason": getattr(e, "reason", None),
                "created_at": getattr(e, "created_at", None),
            }
            for e in (entries or [])
        ]
    except Exception as e:
        exc_name = type(e).__name__
        if "NotFound" in exc_name:
            raise HTTPException(status_code=404, detail={"error": {"code": 404, "message": "Server not found"}})
        elif "Permission" in exc_name:
            raise HTTPException(status_code=403, detail={"error": {"code": 403, "message": str(e)}})
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": str(e)}})


# ==================== Webhook Management ====================

@router.get("/{server_id}/webhooks")
async def get_server_webhooks(server_id: str, current_user: TokenInfo = Depends(get_current_user)):
    """Get all webhooks in a server. Requires manage webhooks permission."""
    webhooks_mod = api.get_webhooks()
    if not webhooks_mod:
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Webhooks module not available"}})

    try:
        sid = int(server_id)
    except ValueError:
        raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": "Invalid server ID"}})

    try:
        webhooks = webhooks_mod.get_server_webhooks(current_user.user_id, sid)
        return [
            {
                "id": str(w.id),
                "channel_id": str(w.channel_id),
                "server_id": str(w.server_id),
                "creator_id": str(getattr(w, "creator_id", 0)),
                "name": w.name,
                "avatar_url": w.avatar_url,
                "created_at": w.created_at,
            }
            for w in (webhooks or [])
        ]
    except Exception as e:
        exc_name = type(e).__name__
        if "NotFound" in exc_name:
            raise HTTPException(status_code=404, detail={"error": {"code": 404, "message": "Server not found"}})
        elif "Permission" in exc_name:
            raise HTTPException(status_code=403, detail={"error": {"code": 403, "message": str(e)}})
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": str(e)}})


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


@router.post("/{server_id}/icon")
async def upload_server_icon(
    server_id: str,
    file: UploadFile = File(...),
    current_user: TokenInfo = Depends(get_current_user)
):
    """Upload a server icon."""
    servers_mod = api.get_servers()
    media = api.get_media()

    if not servers_mod:
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Servers module not available"}})
    if not media:
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Media module not available"}})

    try:
        sid = int(server_id)
    except ValueError:
        raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": "Invalid server ID"}})

    # Check permission
    server = servers_mod.get_server(sid, current_user.user_id)
    if not server:
        raise HTTPException(status_code=404, detail={"error": {"code": 404, "message": "Server not found"}})

    servers_mod.require_permission(current_user.user_id, sid, "server.manage")

    # Use media module for upload (handles validation and security)
    try:
        content = await file.read()
        result = media.upload_file(
            user_id=current_user.user_id,
            file_data=content,
            filename=file.filename or f"server_icon_{sid}",
            content_type=file.content_type
        )

        # Update server with new icon URL
        server = servers_mod.update_server(current_user.user_id, sid, icon_url=result.url)
        return _server_to_response(server)
    except Exception as e:
        exc_name = type(e).__name__
        if "Size" in exc_name or "Type" in exc_name:
            raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": str(e)}})
        elif "Blocked" in exc_name or "Malware" in exc_name:
            raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": str(e)}})
        
        logger.error(f"Server icon upload failed: {e}")
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Upload failed"}})
