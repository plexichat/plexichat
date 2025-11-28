"""
Message routes - Message CRUD endpoints.
"""

from typing import List, Optional
from fastapi import APIRouter, HTTPException, Depends, Query

import src.api as api
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.messages import (
    MessageCreateRequest,
    MessageUpdateRequest,
    MessageResponse,
    AttachmentResponse,
)

router = APIRouter()


def _message_to_response(msg) -> MessageResponse:
    """Convert message object to response model."""
    attachments = []
    if hasattr(msg, "attachments") and msg.attachments:
        for att in msg.attachments:
            attachments.append(AttachmentResponse(
                id=str(att.id),
                filename=att.filename,
                content_type=getattr(att, "content_type", "application/octet-stream"),
                size=getattr(att, "size", 0),
                url=att.url,
            ))
    
    return MessageResponse(
        id=str(msg.id),
        channel_id=str(getattr(msg, "channel_id", 0) or getattr(msg, "conversation_id", 0)),
        author_id=str(msg.author_id),
        content=msg.content,
        created_at=msg.created_at,
        edited_at=getattr(msg, "edited_at", None),
        reply_to_id=str(msg.reply_to_id) if getattr(msg, "reply_to_id", None) else None,
        attachments=attachments,
        embeds=getattr(msg, "embeds", []) or [],
        pinned=getattr(msg, "pinned", False),
    )


@router.get("/channels/{channel_id}/messages", response_model=List[MessageResponse])
async def get_channel_messages(
    channel_id: str,
    limit: int = Query(default=50, ge=1, le=100),
    before: Optional[str] = Query(default=None),
    after: Optional[str] = Query(default=None),
    current_user: TokenInfo = Depends(get_current_user)
):
    """
    Get messages in a channel.
    
    Returns messages with pagination support.
    """
    servers_mod = api.get_servers()
    if not servers_mod:
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Servers module not available"}})
    
    try:
        cid = int(channel_id)
    except ValueError:
        raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": "Invalid channel ID"}})
    
    before_id = int(before) if before else None
    after_id = int(after) if after else None
    
    try:
        messages = servers_mod.get_channel_messages(
            user_id=current_user.user_id,
            channel_id=cid,
            limit=limit,
            before_id=before_id,
            after_id=after_id
        )
        return [_message_to_response(m) for m in messages]
    except Exception as e:
        exc_name = type(e).__name__
        if "NotFound" in exc_name:
            raise HTTPException(status_code=404, detail={"error": {"code": 404, "message": "Channel not found"}})
        elif "Access" in exc_name or "Permission" in exc_name:
            raise HTTPException(status_code=403, detail={"error": {"code": 403, "message": "Access denied"}})
        raise


@router.post("/channels/{channel_id}/messages", response_model=MessageResponse)
async def send_channel_message(
    channel_id: str,
    body: MessageCreateRequest,
    current_user: TokenInfo = Depends(get_current_user)
):
    """
    Send a message to a channel.
    
    Creates a new message in the specified channel.
    """
    servers_mod = api.get_servers()
    if not servers_mod:
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Servers module not available"}})
    
    try:
        cid = int(channel_id)
    except ValueError:
        raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": "Invalid channel ID"}})
    
    if not body.content and not body.attachments and not body.embeds:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Message must have content, attachments, or embeds"}}
        )
    
    reply_to = int(body.reply_to_id) if body.reply_to_id else None
    
    attachments = None
    if body.attachments:
        attachments = [
            {
                "filename": a.filename,
                "content_type": a.content_type,
                "size": a.size,
                "url": a.url,
            }
            for a in body.attachments
        ]
    
    try:
        msg = servers_mod.send_channel_message(
            user_id=current_user.user_id,
            channel_id=cid,
            content=body.content or "",
            reply_to_id=reply_to,
            attachments=attachments
        )
        return _message_to_response(msg)
    except Exception as e:
        exc_name = type(e).__name__
        if "NotFound" in exc_name:
            raise HTTPException(status_code=404, detail={"error": {"code": 404, "message": "Channel not found"}})
        elif "Permission" in exc_name:
            raise HTTPException(status_code=403, detail={"error": {"code": 403, "message": str(e)}})
        elif "Content" in exc_name or "Invalid" in exc_name:
            raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": str(e)}})
        raise


@router.get("/channels/{channel_id}/messages/{message_id}", response_model=MessageResponse)
async def get_message(
    channel_id: str,
    message_id: str,
    current_user: TokenInfo = Depends(get_current_user)
):
    """
    Get a specific message.
    
    Returns the message if the user has access to the channel.
    """
    messaging = api.get_messaging()
    if not messaging:
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Messaging module not available"}})
    
    try:
        mid = int(message_id)
    except ValueError:
        raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": "Invalid message ID"}})
    
    try:
        msg = messaging.get_message(current_user.user_id, mid)
        if not msg:
            raise HTTPException(status_code=404, detail={"error": {"code": 404, "message": "Message not found"}})
        return _message_to_response(msg)
    except HTTPException:
        raise
    except Exception as e:
        exc_name = type(e).__name__
        if "NotFound" in exc_name:
            raise HTTPException(status_code=404, detail={"error": {"code": 404, "message": "Message not found"}})
        elif "Access" in exc_name:
            raise HTTPException(status_code=403, detail={"error": {"code": 403, "message": "Access denied"}})
        raise


@router.patch("/channels/{channel_id}/messages/{message_id}", response_model=MessageResponse)
async def edit_message(
    channel_id: str,
    message_id: str,
    body: MessageUpdateRequest,
    current_user: TokenInfo = Depends(get_current_user)
):
    """
    Edit a message.
    
    Updates the message content. Only the author can edit.
    """
    messaging = api.get_messaging()
    if not messaging:
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Messaging module not available"}})
    
    try:
        mid = int(message_id)
    except ValueError:
        raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": "Invalid message ID"}})
    
    try:
        msg = messaging.edit_message(current_user.user_id, mid, body.content)
        return _message_to_response(msg)
    except Exception as e:
        exc_name = type(e).__name__
        if "NotFound" in exc_name:
            raise HTTPException(status_code=404, detail={"error": {"code": 404, "message": "Message not found"}})
        elif "Access" in exc_name or "Permission" in exc_name:
            raise HTTPException(status_code=403, detail={"error": {"code": 403, "message": str(e)}})
        elif "Content" in exc_name:
            raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": str(e)}})
        raise


@router.delete("/channels/{channel_id}/messages/{message_id}")
async def delete_message(
    channel_id: str,
    message_id: str,
    current_user: TokenInfo = Depends(get_current_user)
):
    """
    Delete a message.
    
    Deletes the message. Author or moderators can delete.
    """
    messaging = api.get_messaging()
    if not messaging:
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Messaging module not available"}})
    
    try:
        mid = int(message_id)
    except ValueError:
        raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": "Invalid message ID"}})
    
    try:
        messaging.delete_message(current_user.user_id, mid)
        return {"success": True}
    except Exception as e:
        exc_name = type(e).__name__
        if "NotFound" in exc_name:
            raise HTTPException(status_code=404, detail={"error": {"code": 404, "message": "Message not found"}})
        elif "Access" in exc_name or "Permission" in exc_name:
            raise HTTPException(status_code=403, detail={"error": {"code": 403, "message": str(e)}})
        raise
