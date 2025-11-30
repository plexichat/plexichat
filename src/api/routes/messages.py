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


def _message_to_response(msg, author_username: str = None) -> MessageResponse:
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
    
    response = MessageResponse(
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
    
    # Add author_username as extra field (not in schema but useful for client)
    response_dict = response.model_dump()
    response_dict["author_username"] = author_username or getattr(msg, "author_username", None) or f"User {msg.author_id}"
    return response_dict


@router.get("/channels/{channel_id}/messages")
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
    Works for both server channels and DM conversations.
    """
    servers_mod = api.get_servers()
    messaging = api.get_messaging()
    auth = api.get_auth()
    
    try:
        cid = int(channel_id)
    except ValueError:
        raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": "Invalid channel ID"}})
    
    before_id = int(before) if before else None
    after_id = int(after) if after else None
    
    messages = None
    
    # Try server channel first
    if servers_mod:
        try:
            messages = servers_mod.get_channel_messages(
                user_id=current_user.user_id,
                channel_id=cid,
                limit=limit,
                before_id=before_id,
                after_id=after_id
            )
        except Exception as e:
            exc_name = type(e).__name__
            if "NotFound" not in exc_name:
                if "Access" in exc_name or "Permission" in exc_name:
                    raise HTTPException(status_code=403, detail={"error": {"code": 403, "message": "Access denied"}})
            # Channel not found in servers, try as DM conversation
            messages = None
    
    # If not a server channel, try as DM conversation
    if messages is None and messaging:
        try:
            messages = messaging.get_messages(
                user_id=current_user.user_id,
                conversation_id=cid,
                limit=limit,
                before_id=before_id,
                after_id=after_id
            )
        except Exception as e:
            exc_name = type(e).__name__
            if "NotFound" in exc_name or "Access" in exc_name:
                raise HTTPException(status_code=404, detail={"error": {"code": 404, "message": "Channel not found"}})
            raise
    
    if messages is None:
        raise HTTPException(status_code=404, detail={"error": {"code": 404, "message": "Channel not found"}})
    
    # Cache author usernames for efficiency
    author_cache = {}
    result = []
    for m in messages:
        author_id = m.author_id
        if author_id not in author_cache:
            username = None
            if auth:
                try:
                    user = auth.get_user(author_id)
                    if user:
                        username = user.username
                except Exception:
                    pass
            author_cache[author_id] = username
        
        result.append(_message_to_response(m, author_cache.get(author_id)))
    
    return result


@router.post("/channels/{channel_id}/messages")
async def send_channel_message(
    channel_id: str,
    body: MessageCreateRequest,
    current_user: TokenInfo = Depends(get_current_user)
):
    """
    Send a message to a channel.
    
    Creates a new message in the specified channel.
    Works for both server channels and DM conversations.
    """
    servers_mod = api.get_servers()
    messaging = api.get_messaging()
    auth = api.get_auth()
    
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
    
    msg = None
    
    # Try server channel first
    if servers_mod:
        try:
            msg = servers_mod.send_channel_message(
                user_id=current_user.user_id,
                channel_id=cid,
                content=body.content or "",
                attachments=attachments
            )
        except Exception as e:
            exc_name = type(e).__name__
            if "NotFound" not in exc_name:
                if "Permission" in exc_name:
                    raise HTTPException(status_code=403, detail={"error": {"code": 403, "message": str(e)}})
                elif "Content" in exc_name or "Invalid" in exc_name:
                    raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": str(e)}})
            # Channel not found in servers, try as DM conversation
            msg = None
    
    # If not a server channel, try as DM conversation
    if msg is None and messaging:
        try:
            msg = messaging.send_message(
                user_id=current_user.user_id,
                conversation_id=cid,
                content=body.content or "",
                attachments=attachments
            )
        except Exception as e:
            exc_name = type(e).__name__
            if "NotFound" in exc_name or "Access" in exc_name:
                raise HTTPException(status_code=404, detail={"error": {"code": 404, "message": "Channel not found"}})
            elif "Content" in exc_name or "Invalid" in exc_name:
                raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": str(e)}})
            raise
    
    if msg is None:
        raise HTTPException(status_code=404, detail={"error": {"code": 404, "message": "Channel not found"}})
    
    # Get author username
    author_username = None
    if auth:
        try:
            user = auth.get_user(current_user.user_id)
            if user:
                author_username = user.username
        except Exception:
            pass
    
    response = _message_to_response(msg, author_username)
    
    # Broadcast MESSAGE_CREATE event via WebSocket
    try:
        from src.api.websocket import get_dispatcher, is_setup as ws_is_setup
        from src.core.events.models import Event
        from src.core.events.types import EventType
        
        if ws_is_setup():
            dispatcher = get_dispatcher()
            
            # Get users to broadcast to
            user_ids = []
            if servers_mod:
                try:
                    # Get server channel info
                    channel = servers_mod.get_channel(cid, current_user.user_id)
                    if channel:
                        server_id = getattr(channel, "server_id", None)
                        if server_id:
                            members = servers_mod.get_members(current_user.user_id, server_id)
                            user_ids = [m.user_id for m in (members or [])]
                except Exception:
                    pass
            
            if not user_ids and messaging:
                # For DM conversations, get participants
                try:
                    participants = messaging.get_participants(cid)
                    user_ids = [p.user_id for p in (participants or [])]
                except Exception:
                    pass
            
            if user_ids:
                event = Event(
                    event_type=EventType.MESSAGE_CREATE,
                    data=response
                )
                await dispatcher.dispatch_event(event, user_ids)
    except Exception as e:
        import utils.logger as logger
        logger.debug(f"Failed to broadcast MESSAGE_CREATE: {e}")
    
    return response


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


@router.post("/channels/{channel_id}/typing")
async def trigger_typing(
    channel_id: str,
    current_user: TokenInfo = Depends(get_current_user)
):
    """
    Trigger typing indicator in a channel.
    
    Broadcasts a typing event to other users in the channel.
    """
    presence = api.get_presence()
    servers_mod = api.get_servers()
    
    try:
        cid = int(channel_id)
    except ValueError:
        raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": "Invalid channel ID"}})
    
    # Verify user has access to channel
    if servers_mod:
        try:
            channel = servers_mod.get_channel(cid, current_user.user_id)
            if not channel:
                raise HTTPException(status_code=404, detail={"error": {"code": 404, "message": "Channel not found"}})
        except HTTPException:
            raise
        except Exception as e:
            if "NotFound" in type(e).__name__:
                raise HTTPException(status_code=404, detail={"error": {"code": 404, "message": "Channel not found"}})
            elif "Access" in type(e).__name__ or "Permission" in type(e).__name__:
                raise HTTPException(status_code=403, detail={"error": {"code": 403, "message": "Access denied"}})
    
    # Set typing indicator in presence module
    if presence:
        try:
            presence.set_typing(current_user.user_id, cid)
        except Exception:
            pass  # Non-critical, don't fail the request
    
    # Broadcast typing event via WebSocket dispatcher
    try:
        from src.api.websocket import get_dispatcher, is_setup as ws_is_setup
        from src.core.events.models import Event
        from src.core.events.types import EventType
        
        if ws_is_setup():
            dispatcher = get_dispatcher()
            auth = api.get_auth()
            user = auth.get_user(current_user.user_id) if auth else None
            
            # Get server members to broadcast to
            if servers_mod and channel:
                server_id = getattr(channel, "server_id", None)
                if server_id:
                    members = servers_mod.get_members(current_user.user_id, server_id)
                    user_ids = [m.user_id for m in (members or []) if m.user_id != current_user.user_id]
                    
                    if user_ids:
                        event = Event(
                            event_type=EventType.TYPING_START,
                            data={
                                "channel_id": str(cid),
                                "user_id": str(current_user.user_id),
                                "username": user.username if user else "Unknown"
                            }
                        )
                        await dispatcher.dispatch_event(event, user_ids)
    except Exception:
        pass  # Non-critical
    
    return {"success": True}
