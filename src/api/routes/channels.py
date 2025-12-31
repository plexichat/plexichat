"""
Channel routes - Channel management endpoints.
"""


from typing import Optional, Dict, Any
from fastapi import APIRouter, HTTPException, Depends, UploadFile, File

import src.api as api
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.servers import ChannelResponse, ChannelUpdateRequest
from src.api.schemas.common import SnowflakeID

import utils.config as config
import utils.logger as logger

router = APIRouter()


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
    except (ValueError, TypeError):
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
    except (ValueError, TypeError):
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
async def delete_channel(channel_id: str, current_user: TokenInfo = Depends(get_current_user)) -> Dict[str, bool]:
    """
    Delete a channel.
    
    Permanently deletes the channel. Requires manage channels permission.
    """
    servers_mod = api.get_servers()
    if not servers_mod:
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Servers module not available"}})

    try:
        cid = int(channel_id)
    except (ValueError, TypeError):
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
async def get_channel_webhooks(channel_id: str, current_user: TokenInfo = Depends(get_current_user)) -> list:
    """Get all webhooks for a channel. Requires manage webhooks permission."""
    webhooks_mod = api.get_webhooks()
    if not webhooks_mod:
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Webhooks module not available"}})

    try:
        cid = int(channel_id)
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": "Invalid channel ID"}})

    try:
        webhooks = webhooks_mod.get_channel_webhooks(current_user.user_id, cid)
        return [
            {
                "id": SnowflakeID(w.id),
                "channel_id": SnowflakeID(w.channel_id),
                "server_id": SnowflakeID(w.server_id),
                "creator_id": SnowflakeID(getattr(w, "creator_id", 0)),
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
    body: Optional[dict] = None,
    current_user: TokenInfo = Depends(get_current_user)
) -> Dict[str, Any]:
    """
    Create an invite for a channel.
    """
    servers_mod = api.get_servers()
    if not servers_mod:
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Servers module not available"}})

    try:
        cid = int(channel_id)
    except (ValueError, TypeError):
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
            "channel_id": SnowflakeID(cid),
            "server_id": SnowflakeID(invite.server_id) if hasattr(invite, "server_id") else None,
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
async def get_invite_info(invite_code: str, current_user: TokenInfo = Depends(get_current_user)) -> Dict[str, Any]:
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
            "server_id": SnowflakeID(invite.server_id) if hasattr(invite, "server_id") else None,
            "server_name": getattr(invite, "server_name", None),
            "channel_id": SnowflakeID(invite.channel_id) if hasattr(invite, "channel_id") else None,
            "inviter_id": SnowflakeID(invite.inviter_id) if hasattr(invite, "inviter_id") else None,
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
async def join_server_via_invite(invite_code: str, current_user: TokenInfo = Depends(get_current_user)) -> Dict[str, Any]:
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
            "server_id": SnowflakeID(result.server_id) if hasattr(result, "server_id") else SnowflakeID(result) if result else None,
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
async def delete_invite(invite_code: str, current_user: TokenInfo = Depends(get_current_user)) -> Dict[str, bool]:
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

# Default upload size limit (10MB)
DEFAULT_UPLOAD_LIMIT = 10 * 1024 * 1024


def _get_upload_limit(user_id: Optional[int] = None) -> int:
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
) -> Dict[str, Any]:
    """
    Upload a file attachment to a channel.
    
    Returns the URL of the uploaded file.
    File size limit is based on user's tier (alpha users get 25MB, premium 100MB, etc.)
    """
    servers_mod = api.get_servers()
    messaging = api.get_messaging()
    media = api.get_media()

    if not media:
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Media module not available"}})

    try:
        cid = int(channel_id)
    except (ValueError, TypeError):
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

    # Use the media module for upload (handles size limits, security, and storage)
    try:
        content = await file.read()
        result = media.upload_file(
            user_id=current_user.user_id,
            file_data=content,
            filename=file.filename or "attachment",
            content_type=file.content_type
        )

        return {
            "id": str(result.file_id),
            "filename": result.filename,
            "size": result.size,
            "content_type": result.content_type,
            "url": result.url,
            "thumbnails": result.thumbnails
        }
    except Exception as e:
        exc_name = type(e).__name__
        if "Size" in exc_name:
            raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": str(e)}})
        elif "Type" in exc_name:
            raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": str(e)}})
        elif "Blocked" in exc_name or "Malware" in exc_name:
            raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": str(e)}})
        
        logger.error(f"Attachment upload failed: {e}")
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Upload failed"}})
