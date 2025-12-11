"""
Channel routes - Channel management endpoints.
"""

from fastapi import APIRouter, HTTPException, Depends

import src.api as api
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.servers import ChannelResponse, ChannelUpdateRequest

router = APIRouter()


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


@router.get("/{channel_id}", response_model=ChannelResponse)
async def get_channel(channel_id: str, current_user: TokenInfo = Depends(get_current_user)):
    """
    Get channel by ID.
    
    Returns channel information if the user has access.
    """
    servers_mod = api.get_servers()
    if not servers_mod:
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Servers module not available"}})

    try:
        cid = int(channel_id)
    except ValueError:
        raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": "Invalid channel ID"}})

    try:
        channel = servers_mod.get_channel(cid, current_user.user_id)
        if not channel:
            raise HTTPException(status_code=404, detail={"error": {"code": 404, "message": "Channel not found"}})
        return _channel_to_response(channel)
    except HTTPException:
        raise
    except Exception as e:
        exc_name = type(e).__name__
        if "NotFound" in exc_name:
            raise HTTPException(status_code=404, detail={"error": {"code": 404, "message": "Channel not found"}})
        elif "Access" in exc_name or "Permission" in exc_name:
            raise HTTPException(status_code=403, detail={"error": {"code": 403, "message": "Access denied"}})
        raise


@router.patch("/{channel_id}", response_model=ChannelResponse)
async def update_channel(
    channel_id: str,
    body: ChannelUpdateRequest,
    current_user: TokenInfo = Depends(get_current_user)
):
    """
    Update channel settings.
    
    Updates channel information. Requires manage channels permission.
    """
    servers_mod = api.get_servers()
    if not servers_mod:
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Servers module not available"}})

    try:
        cid = int(channel_id)
    except ValueError:
        raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": "Invalid channel ID"}})

    try:
        update_data = body.model_dump(exclude_unset=True)
        channel = servers_mod.update_channel(current_user.user_id, cid, **update_data)
        return _channel_to_response(channel)
    except Exception as e:
        exc_name = type(e).__name__
        if "NotFound" in exc_name:
            raise HTTPException(status_code=404, detail={"error": {"code": 404, "message": "Channel not found"}})
        elif "Permission" in exc_name:
            raise HTTPException(status_code=403, detail={"error": {"code": 403, "message": str(e)}})
        raise


@router.delete("/{channel_id}")
async def delete_channel(channel_id: str, current_user: TokenInfo = Depends(get_current_user)):
    """
    Delete a channel.
    
    Permanently deletes the channel. Requires manage channels permission.
    """
    servers_mod = api.get_servers()
    if not servers_mod:
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Servers module not available"}})

    try:
        cid = int(channel_id)
    except ValueError:
        raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": "Invalid channel ID"}})

    try:
        servers_mod.delete_channel(current_user.user_id, cid)
        return {"success": True}
    except Exception as e:
        exc_name = type(e).__name__
        if "NotFound" in exc_name:
            raise HTTPException(status_code=404, detail={"error": {"code": 404, "message": "Channel not found"}})
        elif "Permission" in exc_name:
            raise HTTPException(status_code=403, detail={"error": {"code": 403, "message": str(e)}})
        raise


@router.get("/{channel_id}/webhooks")
async def get_channel_webhooks(channel_id: str, current_user: TokenInfo = Depends(get_current_user)):
    """Get all webhooks for a channel. Requires manage webhooks permission."""
    webhooks_mod = api.get_webhooks()
    if not webhooks_mod:
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Webhooks module not available"}})

    try:
        cid = int(channel_id)
    except ValueError:
        raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": "Invalid channel ID"}})

    try:
        webhooks = webhooks_mod.get_channel_webhooks(current_user.user_id, cid)
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
            raise HTTPException(status_code=404, detail={"error": {"code": 404, "message": "Channel not found"}})
        elif "Permission" in exc_name:
            raise HTTPException(status_code=403, detail={"error": {"code": 403, "message": str(e)}})
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": str(e)}})


@router.post("/{channel_id}/invites")
async def create_channel_invite(
    channel_id: str,
    body: dict = None,
    current_user: TokenInfo = Depends(get_current_user)
):
    """
    Create an invite for a channel.
    """
    servers_mod = api.get_servers()
    if not servers_mod:
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Servers module not available"}})

    try:
        cid = int(channel_id)
    except ValueError:
        raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": "Invalid channel ID"}})

    body = body or {}
    max_age = body.get("max_age", 86400)
    max_uses = body.get("max_uses", 0)
    temporary = body.get("temporary", False)

    try:
        invite = servers_mod.create_invite(
            user_id=current_user.user_id,
            channel_id=cid,
            max_age=max_age,
            max_uses=max_uses,
            temporary=temporary
        )
        return {
            "code": invite.code,
            "channel_id": str(cid),
            "server_id": str(invite.server_id) if hasattr(invite, "server_id") else None,
            "max_age": max_age,
            "max_uses": max_uses,
            "temporary": temporary,
            "uses": 0,
            "created_at": getattr(invite, "created_at", None),
        }
    except Exception as e:
        exc_name = type(e).__name__
        if "NotFound" in exc_name:
            raise HTTPException(status_code=404, detail={"error": {"code": 404, "message": "Channel not found"}})
        elif "Permission" in exc_name:
            raise HTTPException(status_code=403, detail={"error": {"code": 403, "message": str(e)}})
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": str(e)}})



# ==================== Global Invite Routes ====================

@router.get("/invites/{invite_code}")
async def get_invite_info(invite_code: str, current_user: TokenInfo = Depends(get_current_user)):
    """
    Get invite information.
    
    Returns details about an invite without joining.
    """
    servers_mod = api.get_servers()
    if not servers_mod:
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Servers module not available"}})

    try:
        invite = servers_mod.get_invite(invite_code)
        if not invite:
            raise HTTPException(status_code=404, detail={"error": {"code": 404, "message": "Invite not found or expired"}})

        return {
            "code": invite.code,
            "server_id": str(invite.server_id) if hasattr(invite, "server_id") else None,
            "server_name": getattr(invite, "server_name", None),
            "channel_id": str(invite.channel_id) if hasattr(invite, "channel_id") else None,
            "inviter_id": str(invite.inviter_id) if hasattr(invite, "inviter_id") else None,
            "uses": getattr(invite, "uses", 0),
            "max_uses": getattr(invite, "max_uses", 0),
            "expires_at": getattr(invite, "expires_at", None),
        }
    except HTTPException:
        raise
    except Exception as e:
        exc_name = type(e).__name__
        if "NotFound" in exc_name or "Expired" in exc_name:
            raise HTTPException(status_code=404, detail={"error": {"code": 404, "message": "Invite not found or expired"}})
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": str(e)}})


@router.post("/invites/{invite_code}")
async def join_server_via_invite(invite_code: str, current_user: TokenInfo = Depends(get_current_user)):
    """
    Join a server via invite code.
    """
    servers_mod = api.get_servers()
    if not servers_mod:
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Servers module not available"}})

    try:
        result = servers_mod.use_invite(current_user.user_id, invite_code)
        return {
            "success": True,
            "server_id": str(result.server_id) if hasattr(result, "server_id") else str(result) if result else None,
        }
    except Exception as e:
        exc_name = type(e).__name__
        if "NotFound" in exc_name or "Expired" in exc_name:
            raise HTTPException(status_code=404, detail={"error": {"code": 404, "message": "Invite not found or expired"}})
        elif "Already" in exc_name or "Member" in exc_name:
            raise HTTPException(status_code=409, detail={"error": {"code": 409, "message": "Already a member of this server"}})
        elif "Banned" in exc_name:
            raise HTTPException(status_code=403, detail={"error": {"code": 403, "message": "You are banned from this server"}})
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": str(e)}})


@router.delete("/invites/{invite_code}")
async def delete_invite(invite_code: str, current_user: TokenInfo = Depends(get_current_user)):
    """
    Delete an invite.
    
    Requires manage server permission.
    """
    servers_mod = api.get_servers()
    if not servers_mod:
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Servers module not available"}})

    try:
        servers_mod.delete_invite(current_user.user_id, invite_code)
        return {"success": True}
    except Exception as e:
        exc_name = type(e).__name__
        if "NotFound" in exc_name:
            raise HTTPException(status_code=404, detail={"error": {"code": 404, "message": "Invite not found"}})
        elif "Permission" in exc_name:
            raise HTTPException(status_code=403, detail={"error": {"code": 403, "message": str(e)}})
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": str(e)}})


# ==================== Attachment Upload ====================

from fastapi import UploadFile, File
import os
import uuid
from pathlib import Path

import utils.config as config

# Default upload size limit (10MB)
DEFAULT_UPLOAD_LIMIT = 10 * 1024 * 1024


def _get_upload_limit(user_id: int = None) -> int:
    """Get the upload size limit based on user tier or config default."""
    try:
        # If user_id provided, check their tier limits
        if user_id:
            try:
                from src.core import features
                if features.is_setup():
                    tier_limits = features.get_user_tier_limits(user_id)
                    if tier_limits and tier_limits.max_file_size_mb:
                        return tier_limits.max_file_size_mb * 1024 * 1024
            except Exception:
                pass

        # Fall back to config default
        media_config = config.get("media", {})
        size_limits = media_config.get("size_limits", {})
        return size_limits.get("other", DEFAULT_UPLOAD_LIMIT)
    except Exception:
        return DEFAULT_UPLOAD_LIMIT


@router.post("/{channel_id}/attachments")
async def upload_attachment(
    channel_id: str,
    file: UploadFile = File(...),
    current_user: TokenInfo = Depends(get_current_user)
):
    """
    Upload a file attachment to a channel.
    
    Returns the URL of the uploaded file.
    File size limit is based on user's tier (alpha users get 25MB, premium 100MB, etc.)
    """
    servers_mod = api.get_servers()
    messaging = api.get_messaging()

    try:
        cid = int(channel_id)
    except ValueError:
        raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": "Invalid channel ID"}})

    # Verify user has access to channel (try server channel first, then DM)
    has_access = False

    if servers_mod:
        try:
            channel = servers_mod.get_channel(cid, current_user.user_id)
            if channel:
                has_access = True
        except Exception:
            pass

    # If not a server channel, check if it's a DM conversation
    if not has_access and messaging:
        try:
            conv = messaging.get_conversation(cid, current_user.user_id)
            if conv:
                has_access = True
        except Exception:
            pass

    if not has_access:
        raise HTTPException(status_code=404, detail={"error": {"code": 404, "message": "Channel not found"}})

    # Check file size against user's tier limit
    content = await file.read()
    max_size = _get_upload_limit(current_user.user_id)
    if len(content) > max_size:
        max_mb = max_size // (1024 * 1024)
        raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": f"File too large (max {max_mb}MB)"}})

    # Generate unique filename
    ext = os.path.splitext(file.filename)[1] if file.filename else ''
    unique_name = f"{uuid.uuid4().hex}{ext}"

    # Save to media directory
    media_dir = Path.home() / ".plexichat" / "media" / "attachments"
    media_dir.mkdir(parents=True, exist_ok=True)

    file_path = media_dir / unique_name
    with open(file_path, "wb") as f:
        f.write(content)

    # Return URL (relative to API)
    return {
        "id": unique_name,
        "filename": file.filename,
        "size": len(content),
        "content_type": file.content_type,
        "url": f"/api/v1/media/attachments/{unique_name}"
    }
