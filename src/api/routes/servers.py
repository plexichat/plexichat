"""
Server routes - Server/guild management endpoints.
"""

from typing import List
from fastapi import APIRouter, HTTPException, Depends

import src.api as api
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.servers import (
    ServerCreateRequest,
    ServerUpdateRequest,
    ServerResponse,
    ChannelResponse,
)

router = APIRouter()


def _server_to_response(server) -> ServerResponse:
    """Convert server object to response model."""
    return ServerResponse(
        id=str(server.id),
        name=server.name,
        description=getattr(server, "description", None),
        icon_url=getattr(server, "icon_url", None),
        owner_id=str(server.owner_id),
        member_count=getattr(server, "member_count", 0),
        created_at=server.created_at,
    )


def _channel_to_response(channel) -> ChannelResponse:
    """Convert channel object to response model."""
    channel_type = getattr(channel, "channel_type", None)
    if channel_type is not None and hasattr(channel_type, "value"):
        channel_type = channel_type.value
    
    return ChannelResponse(
        id=str(channel.id),
        server_id=str(channel.server_id),
        name=channel.name,
        channel_type=channel_type or "text",
        topic=getattr(channel, "topic", None),
        position=getattr(channel, "position", 0),
        category_id=str(channel.category_id) if getattr(channel, "category_id", None) else None,
        nsfw=getattr(channel, "nsfw", False),
        slowmode_seconds=getattr(channel, "slowmode_seconds", 0),
        created_at=channel.created_at,
    )


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
        servers = servers_mod.get_servers(current_user.user_id)
        return [_server_to_response(s) for s in servers]
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
        channels = servers_mod.get_channels(current_user.user_id, sid)
        return [_channel_to_response(c) for c in channels]
    except Exception as e:
        exc_name = type(e).__name__
        if "NotFound" in exc_name:
            raise HTTPException(status_code=404, detail={"error": {"code": 404, "message": "Server not found"}})
        elif "Access" in exc_name:
            raise HTTPException(status_code=403, detail={"error": {"code": 403, "message": "Access denied"}})
        raise
