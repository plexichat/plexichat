"""
Message routes - Message CRUD endpoints.
"""

from typing import Any, Dict, Optional
from fastapi import APIRouter, HTTPException, Depends, Query

import src.api as api
import utils.logger as logger
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.messages import (
    MessageCreateRequest,
    MessageUpdateRequest,
    MessageResponse,
    AttachmentResponse,
)
from src.api.schemas.common import SnowflakeID

router = APIRouter()


def _message_to_response(
    msg,
    author_username: Optional[str] = None,
    author_avatar_url: Optional[str] = None,
    channel_id: Optional[int] = None,
    reactions_data=None,
) -> MessageResponse:
    """Convert message object to response model."""
    attachments = []
    if hasattr(msg, "attachments") and msg.attachments:
        for att in msg.attachments:
            attachments.append(AttachmentResponse(
                id=SnowflakeID(att.id),
                filename=att.filename,
                content_type=getattr(att, "content_type", "application/octet-stream"),
                size=getattr(att, "size", 0),
                url=att.url,
                hash=getattr(att, "checksum", None) or getattr(att, "hash", None),
            ))

    # Get edited_at from updated_at if message was edited
    edited_at = None
    if getattr(msg, "edited", False) or getattr(msg, "edited_at", None):
        edited_at = getattr(msg, "edited_at", None) or getattr(msg, "updated_at", None)

    # Use explicit channel_id if provided, otherwise fall back to message attributes
    effective_channel_id = channel_id or getattr(msg, "channel_id", 0) or getattr(msg, "conversation_id", 0)

    return MessageResponse(
        id=SnowflakeID(msg.id),
        channel_id=SnowflakeID(effective_channel_id),
        author_id=SnowflakeID(msg.author_id),
        content=msg.content,
        created_at=msg.created_at,
        edited_at=edited_at,
        reply_to_id=SnowflakeID(msg.reply_to_id) if getattr(msg, "reply_to_id", None) else None,
        attachments=attachments,
        embeds=getattr(msg, "embeds", []) or [],
        pinned=getattr(msg, "pinned", False),
        status=getattr(getattr(msg, "status", None), "value", None),
        delivery_count=getattr(msg, "delivery_count", 0),
        read_count=getattr(msg, "read_count", 0),
        author_username=author_username or getattr(msg, "author_username", None) or f"User {msg.author_id}",
        author_avatar_url=author_avatar_url or getattr(msg, "author_avatar_url", None),
        reactions=reactions_data or [],
    )


@router.get("/channels/{channel_id}/messages")
async def get_channel_messages(
    channel_id: str,
    limit: int = Query(default=50, ge=1, le=100),
    before: Optional[SnowflakeID] = Query(default=None),
    after: Optional[SnowflakeID] = Query(default=None),
    current_user: TokenInfo = Depends(get_current_user)
) -> list:
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
            logger.error(f"Error getting messages for channel {cid}: {e}", exc_info=True)
            raise

    if messages is None:
        raise HTTPException(status_code=404, detail={"error": {"code": 404, "message": "Channel not found"}})

    # Bulk fetch all author usernames and avatars in single query (avoids N+1)
    author_ids = list(set(m.author_id for m in messages))
    author_cache = {}  # {user_id: {"username": str, "avatar_url": str}}
    if auth and author_ids:
        try:
            users = auth.get_users_bulk(author_ids)
            author_cache = {
                uid: {"username": u.username, "avatar_url": getattr(u, "avatar_url", None)}
                for uid, u in users.items()
            }
        except Exception:
            pass

    # Fetch reactions for all messages in a single batch query (avoids N+1)
    reactions_module = api.get_reactions()
    reactions_cache = {}
    if reactions_module and messages:
        try:
            message_ids = [m.id for m in messages]
            reactions_cache = reactions_module.get_reactions_batch(current_user.user_id, message_ids)
        except Exception:
            # Fallback to empty reactions if batch fails
            reactions_cache = {m.id: [] for m in messages}

    result = []
    for m in messages:
        author_info = author_cache.get(m.author_id, {})
        result.append(_message_to_response(
            m,
            author_username=author_info.get("username"),
            author_avatar_url=author_info.get("avatar_url"),
            reactions_data=reactions_cache.get(m.id, [])
        ))

    return result


@router.get("/channels/{channel_id}/messages/search")
async def search_messages(
    channel_id: str,
    content: str = Query(..., description="Search query"),
    limit: int = Query(default=25, ge=1, le=100),
    current_user: TokenInfo = Depends(get_current_user)
) -> list:
    """Search messages in a channel by content."""
    messaging = api.get_messaging()
    servers_mod = api.get_servers()
    auth = api.get_auth()

    try:
        cid = int(channel_id)
    except ValueError:
        raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": "Invalid channel ID"}})

    messages = []

    # Try server channel first
    if servers_mod:
        try:
            all_messages = servers_mod.get_channel_messages(
                user_id=current_user.user_id,
                channel_id=cid,
                limit=500  # Get more messages to search through
            )
            if all_messages:
                search_lower = content.lower()
                messages = [m for m in all_messages if search_lower in (m.content or "").lower()][:limit]
        except Exception:
            pass

    # If not found in server channels, try DM conversations
    if not messages and messaging:
        try:
            all_messages = messaging.get_messages(
                user_id=current_user.user_id,
                conversation_id=cid,
                limit=500
            )
            if all_messages:
                search_lower = content.lower()
                messages = [m for m in all_messages if search_lower in (m.content or "").lower()][:limit]
        except Exception:
            pass

    author_cache = {}  # {user_id: {"username": str, "avatar_url": str}}
    result = []
    for m in messages:
        author_id = m.author_id
        if author_id not in author_cache:
            author_info = {"username": None, "avatar_url": None}
            if auth:
                try:
                    user = auth.get_user(author_id)
                    if user:
                        author_info["username"] = user.username
                        author_info["avatar_url"] = getattr(user, "avatar_url", None)
                except Exception:
                    pass
            author_cache[author_id] = author_info
        info = author_cache.get(author_id, {})
        result.append(_message_to_response(m, info.get("username"), info.get("avatar_url")))

    return result


@router.post("/channels/{channel_id}/messages")
async def send_channel_message(
    channel_id: str,
    body: MessageCreateRequest,
    current_user: TokenInfo = Depends(get_current_user)
) -> MessageResponse:
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
    server_id = None  # Track server_id for broadcast optimization
    conversation_id = None  # Track conversation_id to avoid redundant lookups

    # Try server channel first - optimized to avoid duplicate get_channel calls
    if servers_mod:
        try:
            # Get channel info once (this does permission check internally)
            channel = servers_mod.get_channel(cid, current_user.user_id)
            if channel:
                server_id = getattr(channel, "server_id", None)
                conversation_id = getattr(channel, "conversation_id", None)

                # Send directly to messaging module using cached conversation_id
                if conversation_id and messaging:
                    msg = messaging.send_message(
                        user_id=current_user.user_id,
                        conversation_id=conversation_id,
                        content=body.content or "",
                        reply_to_id=reply_to,
                        attachments=attachments,
                        embeds=body.embeds
                    )
                else:
                    # Found server channel but it has no conversation_id
                    logger.warning(f"Server channel {cid} has no conversation_id linked")
                    # Fall back to trying the channel ID as conversation ID (backward compatibility)
                    if messaging:
                        try:
                            msg = messaging.send_message(
                                user_id=current_user.user_id,
                                conversation_id=cid,
                                content=body.content or "",
                                reply_to_id=reply_to,
                                attachments=attachments,
                                embeds=body.embeds
                            )
                        except Exception:
                            msg = None
        except Exception as e:
            exc_name = type(e).__name__
            if "NotFound" not in exc_name:
                if "Permission" in exc_name or "Access" in exc_name:
                    raise HTTPException(status_code=403, detail={"error": {"code": 403, "message": str(e)}})
                elif "Content" in exc_name or "Invalid" in exc_name:
                    raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": str(e)}})
                # For other errors, log and potentially re-raise
                logger.error(f"Error sending message in server channel {cid}: {e}", exc_info=True)
                raise
            # Channel not found in servers, fall through to try as DM conversation
            msg = None

    # If not a server channel, try as DM conversation
    if msg is None and messaging:
        try:
            msg = messaging.send_message(
                user_id=current_user.user_id,
                conversation_id=cid,
                content=body.content or "",
                reply_to_id=reply_to,
                attachments=attachments,
                embeds=body.embeds
            )
        except Exception as e:
            exc_name = type(e).__name__
            if "NotFound" in exc_name or "Access" in exc_name:
                raise HTTPException(status_code=404, detail={"error": {"code": 404, "message": "Channel not found"}})
            elif "Content" in exc_name or "Invalid" in exc_name:
                raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": str(e)}})
            logger.error(f"Error sending message in channel {cid}: {e}", exc_info=True)
            raise

    if msg is None:
        raise HTTPException(status_code=404, detail={"error": {"code": 404, "message": "Channel not found"}})

    # Use username and avatar from token/auth - no need for extra DB lookup!
    author_username = current_user.username
    author_avatar_url = getattr(current_user, "avatar_url", None)

    # If avatar not in token, try to get from auth
    if not author_avatar_url:
        auth = api.get_auth()
        if auth:
            try:
                user = auth.get_user(current_user.user_id)
                if user:
                    author_avatar_url = getattr(user, "avatar_url", None)
            except Exception:
                pass

    response = _message_to_response(msg, author_username, author_avatar_url, channel_id=cid)

    # Broadcast MESSAGE_CREATE event via WebSocket (fully async - doesn't block response)
    try:
        from src.api.websocket import get_dispatcher, is_setup as ws_is_setup
        from src.core.events.models import Event
        from src.core.events.types import EventType
        import asyncio

        if ws_is_setup():
            dispatcher = get_dispatcher()

            # Create async task that handles everything - response returns immediately
            async def broadcast_message():
                try:
                    # Get users to broadcast to - this is now async and doesn't block response
                    user_ids = []
                    logger.info(f"broadcast_message: server_id={server_id}, cid={cid}")

                    if server_id and servers_mod:
                        try:
                            user_ids = servers_mod.get_member_user_ids(server_id)
                            logger.info(f"Got {len(user_ids)} server members for server {server_id}")
                        except Exception as e:
                            logger.warning(f"Failed to get server members: {e}")

                    if not user_ids and messaging:
                        try:
                            participants = messaging.get_participants(current_user.user_id, cid)
                            user_ids = [p.user_id for p in (participants or [])]
                            logger.info(f"Got {len(user_ids)} DM participants for channel {cid}")
                        except Exception as e:
                            logger.warning(f"Failed to get DM participants: {e}")

                    if user_ids:
                        logger.info(f"Broadcasting MESSAGE_CREATE to {len(user_ids)} users for channel {cid}: {user_ids[:5]}...")
                        event = Event(
                            event_type=EventType.MESSAGE_CREATE,
                            data=response.model_dump(),
                            server_id=server_id,  # Set for proper intent filtering
                            channel_id=cid,
                        )
                        count = await dispatcher.dispatch_event(event, user_ids)
                        logger.info(f"MESSAGE_CREATE dispatched to {count} connections")
                    else:
                        logger.warning(f"No user_ids found for MESSAGE_CREATE broadcast in channel {cid}")
                except Exception as e:
                    logger.warning(f"Failed to broadcast MESSAGE_CREATE: {e}", exc_info=True)

            # Schedule the broadcast task
            asyncio.create_task(broadcast_message())
            logger.info(f"Created broadcast task for message in channel {cid}")
    except Exception as e:
        logger.warning(f"Failed to setup MESSAGE_CREATE broadcast: {e}", exc_info=True)

    return response


@router.get("/channels/{channel_id}/messages/unread")
async def get_unread_count(
    channel_id: str,
    current_user: TokenInfo = Depends(get_current_user)
) -> Dict[str, Any]:
    """
    Get unread message count for a channel.
    """
    messaging = api.get_messaging()
    if not messaging:
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Messaging module not available"}})

    try:
        cid = int(channel_id)
    except ValueError:
        raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": "Invalid channel ID"}})

    try:
        counts = messaging.get_unread_count(current_user.user_id, cid)
        return {"channel_id": channel_id, "unread_count": counts.get(cid, 0)}
    except Exception as e:
        exc_name = type(e).__name__
        if "NotFound" in exc_name or "Access" in exc_name:
            raise HTTPException(status_code=404, detail={"error": {"code": 404, "message": "Channel not found"}})
        logger.error(f"Error getting unread count for channel {cid}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": str(e)}})


@router.post("/channels/{channel_id}/messages/ack")
async def acknowledge_messages(
    channel_id: str,
    message_id: Optional[str] = Query(default=None, description="Mark as read up to this message ID"),
    current_user: TokenInfo = Depends(get_current_user)
) -> Dict[str, Any]:
    """
    Mark messages as read in a channel (read receipts).
    
    If message_id is provided, marks all messages up to and including that message as read.
    If not provided, marks all messages in the channel as read.
    """
    import asyncio

    messaging = api.get_messaging()
    if not messaging:
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Messaging module not available"}})

    try:
        cid = int(channel_id)
    except ValueError:
        raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": "Invalid channel ID"}})

    up_to_id = int(message_id) if message_id else None

    try:
        count = messaging.mark_read(current_user.user_id, cid, up_to_id)
        logger.debug(f"User {current_user.user_id} marked {count} messages as read in channel {cid}")

        # Broadcast read receipt event via WebSocket (fire and forget)
        import asyncio

        async def dispatch_ack():
            try:
                from src.api.websocket import get_dispatcher, is_setup as ws_is_setup
                from src.core.events.models import Event
                from src.core.events.types import EventType

                if ws_is_setup():
                    dispatcher = get_dispatcher()
                    servers_mod = api.get_servers()

                    user_ids = []
                    if servers_mod:
                        try:
                            channel = servers_mod.get_channel(cid, current_user.user_id)
                            if channel:
                                server_id = getattr(channel, "server_id", None)
                                if server_id:
                                    user_ids = servers_mod.get_member_user_ids(server_id)
                        except Exception:
                            pass

                    if not user_ids and messaging:
                        try:
                            participants = messaging.get_participants(current_user.user_id, cid)
                            user_ids = [p.user_id for p in (participants or [])]
                        except Exception:
                            pass

                    if user_ids:
                        event = Event(
                            event_type=EventType.MESSAGE_ACK,
                            data={
                                "channel_id": str(cid),
                                "user_id": str(current_user.user_id),
                                "message_id": str(up_to_id) if up_to_id else None,
                            }
                        )
                        await dispatcher.dispatch_event(event, user_ids)
            except Exception as e:
                logger.debug(f"Failed to broadcast MESSAGE_ACK: {e}")

        asyncio.create_task(dispatch_ack())

        return {"success": True, "messages_marked": count}
    except Exception as e:
        exc_name = type(e).__name__
        if "NotFound" in exc_name or "Access" in exc_name:
            raise HTTPException(status_code=404, detail={"error": {"code": 404, "message": "Channel not found"}})
        logger.error(f"Error acknowledging messages in channel {cid}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": str(e)}})


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
        return _message_to_response(msg).model_dump()
    except HTTPException:
        raise
    except Exception as e:
        exc_name = type(e).__name__
        if "NotFound" in exc_name:
            raise HTTPException(status_code=404, detail={"error": {"code": 404, "message": "Message not found"}})
        elif "Access" in exc_name:
            raise HTTPException(status_code=403, detail={"error": {"code": 403, "message": "Access denied"}})
        logger.error(f"Error getting message {mid}: {e}", exc_info=True)
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
    servers_mod = api.get_servers()
    auth = api.get_auth()

    if not messaging:
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Messaging module not available"}})

    try:
        mid = int(message_id)
        cid = int(channel_id)
    except ValueError:
        raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": "Invalid message or channel ID"}})

    try:
        msg = messaging.edit_message(current_user.user_id, mid, body.content)

        # Get author username and avatar
        author_username = None
        author_avatar_url = None
        if auth:
            try:
                user = auth.get_user(current_user.user_id)
                if user:
                    author_username = user.username
                    author_avatar_url = getattr(user, "avatar_url", None)
            except Exception:
                pass

        response = _message_to_response(msg, author_username, author_avatar_url, channel_id=cid)

        # Broadcast MESSAGE_UPDATE event via WebSocket (fire and forget)
        import asyncio

        async def dispatch_message_update():
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
                            channel = servers_mod.get_channel(cid, current_user.user_id)
                            if channel:
                                server_id = getattr(channel, "server_id", None)
                                if server_id:
                                    user_ids = servers_mod.get_member_user_ids(server_id)
                        except Exception:
                            pass

                    if not user_ids and messaging:
                        try:
                            participants = messaging.get_participants(current_user.user_id, cid)
                            user_ids = [p.user_id for p in (participants or [])]
                        except Exception:
                            pass

                    if user_ids:
                        # Determine server_id for intent filtering
                        event_server_id = None
                        if servers_mod:
                            try:
                                channel = servers_mod.get_channel(cid, current_user.user_id)
                                if channel:
                                    event_server_id = getattr(channel, "server_id", None)
                            except Exception:
                                pass

                        event = Event(
                            event_type=EventType.MESSAGE_UPDATE,
                            data=response.model_dump(),
                            server_id=event_server_id,
                            channel_id=cid,
                        )
                        await dispatcher.dispatch_event(event, user_ids)
            except Exception as e:
                logger.debug(f"Failed to broadcast MESSAGE_UPDATE: {e}")

        asyncio.create_task(dispatch_message_update())

        return response
    except Exception as e:
        exc_name = type(e).__name__
        if "NotFound" in exc_name:
            raise HTTPException(status_code=404, detail={"error": {"code": 404, "message": "Message not found"}})
        elif "Access" in exc_name or "Permission" in exc_name:
            raise HTTPException(status_code=403, detail={"error": {"code": 403, "message": str(e)}})
        elif "Content" in exc_name:
            raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": str(e)}})
        logger.error(f"Error editing message {mid}: {e}", exc_info=True)
        raise


@router.delete("/channels/{channel_id}/messages/{message_id}")
async def delete_message(
    channel_id: str,
    message_id: str,
    current_user: TokenInfo = Depends(get_current_user)
) -> Dict[str, bool]:
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
        # Get message details BEFORE deleting for broadcast
        msg = messaging.get_message(current_user.user_id, mid)
        if not msg:
            raise HTTPException(status_code=404, detail={"error": {"code": 404, "message": "Message not found"}})
        
        cid = msg.conversation_id
        
        messaging.delete_message(current_user.user_id, mid)

        # Broadcast MESSAGE_DELETE event via WebSocket (fire and forget)
        import asyncio

        async def dispatch_delete():
            try:
                from src.api.websocket import get_dispatcher, is_setup as ws_is_setup
                from src.core.events.models import Event
                from src.core.events.types import EventType

                if ws_is_setup():
                    dispatcher = get_dispatcher()
                    servers_mod = api.get_servers()

                    # Determine server_id for intent filtering
                    server_id = None
                    user_ids = []
                    if servers_mod:
                        try:
                            channel = servers_mod.get_channel(cid, current_user.user_id)
                            if channel:
                                server_id = getattr(channel, "server_id", None)
                                if server_id:
                                    user_ids = servers_mod.get_member_user_ids(server_id)
                        except Exception:
                            pass

                    if not user_ids and messaging:
                        try:
                            participants = messaging.get_participants(current_user.user_id, cid)
                            user_ids = [p.user_id for p in (participants or [])]
                        except Exception:
                            pass

                    if user_ids:
                        event = Event(
                            event_type=EventType.MESSAGE_DELETE,
                            data={
                                "id": str(mid),
                                "channel_id": str(cid),
                                "server_id": str(server_id) if server_id else None
                            },
                            server_id=server_id,
                            channel_id=cid,
                        )
                        await dispatcher.dispatch_event(event, user_ids)
            except Exception as e:
                logger.debug(f"Failed to broadcast MESSAGE_DELETE: {e}")

        asyncio.create_task(dispatch_delete())

        return {"success": True}
    except Exception as e:
        exc_name = type(e).__name__
        if "NotFound" in exc_name:
            raise HTTPException(status_code=404, detail={"error": {"code": 404, "message": "Message not found"}})
        elif "Access" in exc_name or "Permission" in exc_name:
            raise HTTPException(status_code=403, detail={"error": {"code": 403, "message": str(e)}})
        logger.error(f"Error deleting message {mid}: {e}", exc_info=True)
        raise


@router.get("/channels/{channel_id}/pins")
async def get_pinned_messages(
    channel_id: str,
    current_user: TokenInfo = Depends(get_current_user)
) -> list:
    """Get all pinned messages in a channel."""
    messaging = api.get_messaging()
    servers_mod = api.get_servers()
    auth = api.get_auth()

    try:
        cid = int(channel_id)
    except ValueError:
        raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": "Invalid channel ID"}})

    messages = []

    # Try server channel first - get all messages and filter pinned
    if servers_mod:
        try:
            all_messages = servers_mod.get_channel_messages(
                user_id=current_user.user_id,
                channel_id=cid,
                limit=500
            )
            if all_messages:
                messages = [m for m in all_messages if getattr(m, "pinned", False)]
        except Exception:
            pass

    # If not a server channel, try DM conversation
    if not messages and messaging:
        try:
            messages = messaging.get_pinned_messages(current_user.user_id, cid) or []
        except Exception:
            pass

    author_cache = {}  # {user_id: {"username": str, "avatar_url": str}}
    result = []
    for m in messages:
        author_id = m.author_id
        if author_id not in author_cache:
            author_info = {"username": None, "avatar_url": None}
            if auth:
                try:
                    user = auth.get_user(author_id)
                    if user:
                        author_info["username"] = user.username
                        author_info["avatar_url"] = getattr(user, "avatar_url", None)
                except Exception:
                    pass
            author_cache[author_id] = author_info
        info = author_cache.get(author_id, {})
        result.append(_message_to_response(m, info.get("username"), info.get("avatar_url")))

    return result


@router.put("/channels/{channel_id}/pins/{message_id}")
async def pin_message(
    channel_id: str,
    message_id: str,
    current_user: TokenInfo = Depends(get_current_user)
) -> Dict[str, bool]:
    """Pin a message in a channel."""
    messaging = api.get_messaging()

    try:
        mid = int(message_id)
    except ValueError:
        raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": "Invalid message ID"}})

    try:
        if messaging:
            messaging.pin_message(current_user.user_id, mid)
            
            # Fetch updated message for broadcast
            msg = messaging.get_message(current_user.user_id, mid)
            if msg:
                response = _message_to_response(msg, channel_id=cid)
                
                # Broadcast update (fire and forget)
                import asyncio
                async def dispatch_pin():
                    try:
                        from src.api.websocket import get_dispatcher, is_setup as ws_is_setup
                        from src.core.events.models import Event
                        from src.core.events.types import EventType

                        if ws_is_setup():
                            dispatcher = get_dispatcher()
                            servers_mod = api.get_servers()

                            user_ids = []
                            server_id = None
                            if servers_mod:
                                try:
                                    channel = servers_mod.get_channel(cid, current_user.user_id)
                                    if channel:
                                        server_id = getattr(channel, "server_id", None)
                                        if server_id:
                                            user_ids = servers_mod.get_member_user_ids(server_id)
                                except Exception: pass

                            if not user_ids and messaging:
                                try:
                                    participants = messaging.get_participants(current_user.user_id, cid)
                                    user_ids = [p.user_id for p in (participants or [])]
                                except Exception: pass

                            if user_ids:
                                event = Event(
                                    event_type=EventType.MESSAGE_UPDATE,
                                    data=response.model_dump(),
                                    server_id=server_id,
                                    channel_id=cid,
                                )
                                await dispatcher.dispatch_event(event, user_ids)
                    except Exception as e:
                        logger.debug(f"Failed to broadcast pin update: {e}")
                
                asyncio.create_task(dispatch_pin())

        return {"success": True}
    except Exception as e:
        exc_name = type(e).__name__
        if "NotFound" in exc_name:
            raise HTTPException(status_code=404, detail={"error": {"code": 404, "message": "Message not found"}})
        elif "Permission" in exc_name:
            raise HTTPException(status_code=403, detail={"error": {"code": 403, "message": str(e)}})
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": str(e)}})


@router.delete("/channels/{channel_id}/pins/{message_id}")
async def unpin_message(
    channel_id: str,
    message_id: str,
    current_user: TokenInfo = Depends(get_current_user)
) -> Dict[str, bool]:
    """Unpin a message from a channel."""
    messaging = api.get_messaging()

    try:
        mid = int(message_id)
    except ValueError:
        raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": "Invalid message ID"}})

    try:
        if messaging:
            messaging.unpin_message(current_user.user_id, mid)

            # Fetch updated message for broadcast
            msg = messaging.get_message(current_user.user_id, mid)
            if msg:
                response = _message_to_response(msg, channel_id=cid)
                
                # Broadcast update (fire and forget)
                import asyncio
                async def dispatch_unpin():
                    try:
                        from src.api.websocket import get_dispatcher, is_setup as ws_is_setup
                        from src.core.events.models import Event
                        from src.core.events.types import EventType

                        if ws_is_setup():
                            dispatcher = get_dispatcher()
                            servers_mod = api.get_servers()

                            user_ids = []
                            server_id = None
                            if servers_mod:
                                try:
                                    channel = servers_mod.get_channel(cid, current_user.user_id)
                                    if channel:
                                        server_id = getattr(channel, "server_id", None)
                                        if server_id:
                                            user_ids = servers_mod.get_member_user_ids(server_id)
                                except Exception: pass

                            if not user_ids and messaging:
                                try:
                                    participants = messaging.get_participants(current_user.user_id, cid)
                                    user_ids = [p.user_id for p in (participants or [])]
                                except Exception: pass

                            if user_ids:
                                event = Event(
                                    event_type=EventType.MESSAGE_UPDATE,
                                    data=response.model_dump(),
                                    server_id=server_id,
                                    channel_id=cid,
                                )
                                await dispatcher.dispatch_event(event, user_ids)
                    except Exception as e:
                        logger.debug(f"Failed to broadcast unpin update: {e}")
                
                asyncio.create_task(dispatch_unpin())

        return {"success": True}
    except Exception as e:
        exc_name = type(e).__name__
        if "NotFound" in exc_name:
            raise HTTPException(status_code=404, detail={"error": {"code": 404, "message": "Message not found"}})
        elif "Permission" in exc_name:
            raise HTTPException(status_code=403, detail={"error": {"code": 403, "message": str(e)}})
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": str(e)}})


@router.get("/users/@me/unread")
async def get_all_unread_counts(
    current_user: TokenInfo = Depends(get_current_user)
) -> Dict[str, Any]:
    """
    Get unread message counts for all conversations.
    """
    messaging = api.get_messaging()
    if not messaging:
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Messaging module not available"}})

    try:
        counts = messaging.get_unread_count(current_user.user_id)
        # Convert int keys to string for JSON
        return {"unread_counts": {str(k): v for k, v in counts.items()}}
    except Exception as e:
        logger.error(f"Error getting all unread counts: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": str(e)}})


@router.post("/channels/{channel_id}/typing")
async def trigger_typing(
    channel_id: str,
    current_user: TokenInfo = Depends(get_current_user)
) -> Dict[str, bool]:
    """
    Trigger typing indicator in a channel.
    
    Broadcasts a typing event to other users in the channel.
    Works for both server channels and DM conversations.
    """
    presence = api.get_presence()
    servers_mod = api.get_servers()
    messaging = api.get_messaging()

    try:
        cid = int(channel_id)
    except ValueError:
        raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": "Invalid channel ID"}})

    channel = None
    user_ids = []

    # Try server channel first
    if servers_mod:
        try:
            channel = servers_mod.get_channel(cid, current_user.user_id)
            if channel:
                server_id = getattr(channel, "server_id", None)
                if server_id:
                    # Use optimized function that only fetches user IDs
                    user_ids = servers_mod.get_member_user_ids(server_id, exclude_user_id=current_user.user_id)
        except Exception:
            channel = None

    # If not a server channel, try as DM conversation
    if not channel and messaging:
        try:
            participants = messaging.get_participants(current_user.user_id, cid)
            if participants:
                user_ids = [p.user_id for p in participants if p.user_id != current_user.user_id]
        except Exception:
            pass

    # Set typing indicator in presence module
    if presence:
        try:
            presence.start_typing(current_user.user_id, cid)
        except Exception:
            pass  # Non-critical, don't fail the request

    # Broadcast typing event via WebSocket dispatcher (fire and forget)
    if user_ids:
        import asyncio

        # Capture username from token - no extra DB lookup needed!
        username = current_user.username

        async def dispatch_typing():
            try:
                from src.api.websocket import get_dispatcher, is_setup as ws_is_setup
                from src.core.events.models import Event
                from src.core.events.types import EventType

                if ws_is_setup():
                    dispatcher = get_dispatcher()

                    # Determine server_id for intent filtering
                    event_server_id = None
                    if channel:
                        event_server_id = getattr(channel, "server_id", None)

                    event = Event(
                        event_type=EventType.TYPING_START,
                        data={
                            "channel_id": str(cid),
                            "user_id": str(current_user.user_id),
                            "username": username
                        },
                        server_id=event_server_id,
                        channel_id=cid,
                    )
                    await dispatcher.dispatch_event(event, user_ids)
            except Exception as e:
                logger.debug(f"Failed to dispatch typing event: {e}")

        # Fire and forget - don't wait for dispatch to complete
        asyncio.create_task(dispatch_typing())

    return {"success": True}