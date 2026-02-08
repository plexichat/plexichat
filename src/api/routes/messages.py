"""
Message routes - Message CRUD endpoints.
"""

from typing import Optional, List
from fastapi import APIRouter, HTTPException, Depends, Query

import src.api as api
import utils.logger as logger
from src.core.database import cached
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.messages import (
    MessageCreateRequest,
    MessageUpdateRequest,
    MessageResponse,
    AttachmentResponse,
    UnreadCountResponse,
    AllUnreadCountsResponse,
    AckResponse,
)
from src.api.schemas.common import SnowflakeID, ErrorResponse, SuccessResponse
from src.core.messaging.exceptions import AttachmentLimitError

router = APIRouter(tags=["Messages"])


def _message_to_response(
    msg,
    author_username: Optional[str] = None,
    author_avatar_url: Optional[str] = None,
    author_badges: Optional[List[str]] = None,
    channel_id: Optional[int] = None,
    reactions_data=None,
    read_by_usernames: Optional[List[str]] = None,
    media_mod=None,
) -> MessageResponse:
    """Convert message object to response model."""
    # Handle dict vs object for msg
    def get_attr(obj, name, default=None):
        if isinstance(obj, dict):
            return obj.get(name, default)
        return getattr(obj, name, default)

    msg_id = get_attr(msg, "id")
    author_id = get_attr(msg, "author_id")
    content = get_attr(msg, "content", "")
    created_at = get_attr(msg, "created_at")
    
    attachments = []
    msg_attachments = get_attr(msg, "attachments")
    if msg_attachments:
        for att in msg_attachments:
            # att might also be a dict
            att_id = get_attr(att, "id")
            att_filename = get_attr(att, "filename", "attachment")
            att_url = get_attr(att, "url")
            att_content_type = get_attr(att, "content_type", "application/octet-stream")
            att_size = get_attr(att, "size", 0)
            att_hash = get_attr(att, "checksum") or get_attr(att, "hash")

            # Handle URL signing for absolute security and cross-origin access
            url = att_url
            if media_mod and url and url.startswith("/api/v1/media/attachments/"):
                try:
                    # Parse file_id from metadata if available, or try to lookup
                    file_id = get_attr(att, "file_id")
                    if not file_id:
                        metadata = get_attr(att, "metadata")
                        if metadata:
                            if isinstance(metadata, dict):
                                file_id = metadata.get("file_id")
                            elif isinstance(metadata, str):
                                import json
                                try:
                                    meta = json.loads(metadata)
                                    file_id = meta.get("file_id")
                                except Exception:
                                    pass
                    
                    if file_id:
                        signed = media_mod.sign_url(int(file_id))
                        url = signed.url
                except Exception as e:
                    logger.debug(f"Failed to sign attachment URL: {e}")

            attachments.append(
                AttachmentResponse(
                    id=SnowflakeID(att_id),
                    filename=att_filename,
                    content_type=att_content_type,
                    size=att_size,
                    url=url,
                    hash=att_hash,
                )
            )

    # Get edited_at from updated_at if message was edited
    edited_at = None
    if get_attr(msg, "edited", False) or get_attr(msg, "edited_at"):
        edited_at = get_attr(msg, "edited_at") or get_attr(msg, "updated_at")

    # Use explicit channel_id if provided, otherwise fall back to message attributes
    effective_channel_id = (
        channel_id
        or get_attr(msg, "channel_id")
        or get_attr(msg, "conversation_id")
        or 0
    )

    # Use provided reader usernames (must be bulk-fetched by caller for performance)
    read_by = read_by_usernames or []
    read_count = len(read_by) if read_by_usernames is not None else get_attr(msg, "read_count", 0)

    return MessageResponse(
        id=SnowflakeID(msg_id),
        channel_id=SnowflakeID(effective_channel_id),
        author_id=SnowflakeID(author_id),
        content=content,
        created_at=created_at,
        edited_at=edited_at,
        reply_to_id=SnowflakeID(get_attr(msg, "reply_to_id"))
        if get_attr(msg, "reply_to_id")
        else None,
        attachments=attachments,
        embeds=get_attr(msg, "embeds", []) or [],
        pinned=get_attr(msg, "pinned", False),
        status=getattr(get_attr(msg, "status"), "value", get_attr(msg, "status")) if get_attr(msg, "status") else None,
        delivery_count=get_attr(msg, "delivery_count", 0),
        read_count=read_count,
        read_by=read_by,
        author_username=author_username
        or get_attr(msg, "author_username")
        or f"User {author_id}",
        author_avatar_url=author_avatar_url or get_attr(msg, "author_avatar_url"),
        author_badges=author_badges or get_attr(msg, "author_badges") or [],
        reactions=reactions_data or [],
    )


@router.get(
    "/channels/{channel_id}/messages",
    response_model=List[MessageResponse],
    summary="Get channel messages",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid channel ID"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        404: {"model": ErrorResponse, "description": "Channel not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
@cached(ttl=10, prefix="messages_api")
def get_channel_messages(
    channel_id: str,
    limit: int = Query(default=50, ge=1, le=100),
    before: Optional[SnowflakeID] = Query(default=None),
    after: Optional[SnowflakeID] = Query(default=None),
    current_user: TokenInfo = Depends(get_current_user),
) -> List[MessageResponse]:
    """
    Get messages in a channel.

    Returns messages with pagination support.
    Works for both server channels and DM conversations.
    """
    servers_mod = api.get_servers()
    messaging = api.get_messaging()
    auth = api.get_auth()

    try:
        try:
            cid = int(channel_id)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid channel ID"}},
            )

        before_id = int(before) if before else None
        after_id = int(after) if after else None

        messages = None

        # Try server channel first (more common)
        if servers_mod:
            try:
                messages = servers_mod.get_channel_messages(
                    user_id=current_user.user_id,
                    channel_id=cid,
                    limit=limit,
                    before_id=before_id,
                    after_id=after_id,
                )
            except Exception:
                # If not a server channel or no access, fall back to messaging
                messages = None

        # Fallback to DM/Conversation
        if messages is None and messaging:
            try:
                messages = messaging.get_messages(
                    user_id=current_user.user_id,
                    conversation_id=cid,
                    limit=limit,
                    before_id=before_id,
                    after_id=after_id,
                )
            except Exception as e:
                exc_name = type(e).__name__
                if "NotFound" in exc_name or "Access" in exc_name:
                    raise HTTPException(
                        status_code=404,
                        detail={"error": {"code": 404, "message": "Channel not found"}},
                    )
                raise

        if messages is None:
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": 404, "message": "Channel not found"}},
            )

        # Bulk fetch all author usernames and avatars in single query (avoids N+1)
        author_ids = list(set(m.author_id for m in messages))
        author_cache = {}  # {user_id: {"username": str, "avatar_url": str, "badges": list}}
        if auth and author_ids:
            try:
                users = auth.get_user_profiles_bulk(author_ids)
                author_cache = {
                    uid: {
                        "username": u["username"],
                        "avatar_url": u.get("avatar_url"),
                        "badges": u.get("badges", []),
                    }
                    for uid, u in users.items()
                }
            except Exception:
                pass

        # Get media module for URL signing
        media_mod = api.get_media()

        # Fetch reactions for all messages in a single batch query (avoids N+1)
        reactions_module = api.get_reactions()
        reactions_cache = {}
        if reactions_module and messages:
            try:
                message_ids = [m.id for m in messages]
                reactions_cache = reactions_module.get_reactions_batch(
                    current_user.user_id, message_ids
                )
            except Exception:
                # Fallback to empty reactions if batch fails
                reactions_cache = {m.id: [] for m in messages}

        # Bulk fetch reader information for messages authored by current user (sender only)
        # This eliminates the N+1 problem in _message_to_response
        readers_cache = {} # {message_id: [username, ...]}
        if messaging and auth and messages:
            try:
                # Only check messages authored by current user
                own_message_ids = []
                for m in messages:
                    mid = getattr(m, "id", None) or m.get("id")
                    author_id = getattr(m, "author_id", None) or m.get("author_id")
                    if int(author_id) == int(current_user.user_id):
                        own_message_ids.append(mid)

                if own_message_ids:
                    reader_ids_map = messaging.get_batch_reader_ids(current_user.user_id, own_message_ids)
                    
                    # Collect all unique reader IDs to fetch usernames in bulk
                    all_reader_ids = set()
                    for r_ids in reader_ids_map.values():
                        all_reader_ids.update(r_ids)
                    
                    if all_reader_ids:
                        reader_users = auth.get_user_profiles_bulk(list(all_reader_ids))
                        
                        # Build the readers cache with usernames
                        for mid, r_ids in reader_ids_map.items():
                            readers_cache[mid] = [
                                reader_users[rid]["username"] 
                                for rid in r_ids if rid in reader_users
                            ]
            except Exception as e:
                logger.warning(f"Failed to bulk fetch reader info: {e}")

        result = []
        for m in messages:
            # Robust lookup: check both string and int keys
            author_id = getattr(m, "author_id", None) or m.get("author_id")
            mid = getattr(m, "id", None) or m.get("id")
            author_info = author_cache.get(author_id) or author_cache.get(str(author_id)) or {}
            
            result.append(
                _message_to_response(
                    m,
                    author_username=author_info.get("username"),
                    author_avatar_url=author_info.get("avatar_url"),
                    author_badges=author_info.get("badges"),
                    reactions_data=reactions_cache.get(mid, []),
                    read_by_usernames=readers_cache.get(mid),
                    media_mod=media_mod,
                )
            )

        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Failed to get messages for channel {channel_id}: {e}", exc_info=True
        )
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )


@router.get(
    "/channels/{channel_id}/messages/search",
    response_model=List[MessageResponse],
    summary="Search messages",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid channel ID"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        404: {"model": ErrorResponse, "description": "Channel not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
@cached(ttl=60, prefix="search_messages_api")
async def search_messages(
    channel_id: str,
    content: str = Query(..., description="Search query"),
    limit: int = Query(default=25, ge=1, le=100),
    current_user: TokenInfo = Depends(get_current_user),
) -> List[MessageResponse]:
    """Search messages in a channel by content."""
    messaging = api.get_messaging()
    servers_mod = api.get_servers()
    auth = api.get_auth()

    try:
        try:
            cid = int(channel_id)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid channel ID"}},
            )

        messages = []

        # Try server channel first
        if servers_mod:
            try:
                all_messages = servers_mod.get_channel_messages(
                    user_id=current_user.user_id,
                    channel_id=cid,
                    limit=500,  # Get more messages to search through
                )
                if all_messages:
                    search_lower = content.lower()
                    messages = [
                        m
                        for m in all_messages
                        if search_lower in (m.content or "").lower()
                    ][:limit]
            except Exception:
                pass

        # If not found in server channels, try DM conversations
        if not messages and messaging:
            try:
                all_messages = messaging.get_messages(
                    user_id=current_user.user_id, conversation_id=cid, limit=500
                )
                if all_messages:
                    search_lower = content.lower()
                    messages = [
                        m
                        for m in all_messages
                        if search_lower in (m.content or "").lower()
                    ][:limit]
            except Exception:
                pass

        # Bulk fetch all author info
        author_ids = list(set(m.author_id for m in messages))
        author_cache = {}
        if auth and author_ids:
            try:
                users = auth.get_user_profiles_bulk(author_ids)
                author_cache = {
                    uid: {
                        "username": u["username"],
                        "avatar_url": u.get("avatar_url"),
                        "badges": u.get("badges", []),
                    }
                    for uid, u in users.items()
                }
            except Exception:
                pass

        media_mod = api.get_media()

        result = []
        for m in messages:
            author_id = m.author_id
            author_info = author_cache.get(author_id) or author_cache.get(str(author_id)) or {}
            result.append(
                _message_to_response(
                    m, 
                    author_username=author_info.get("username"), 
                    author_avatar_url=author_info.get("avatar_url"),
                    author_badges=author_info.get("badges"),
                    media_mod=media_mod
                )
            )

        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Failed to search messages in channel {channel_id}: {e}", exc_info=True
        )
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )


@router.post(
    "/channels/{channel_id}/messages",
    response_model=MessageResponse,
    summary="Send message",
    responses={
        400: {
            "model": ErrorResponse,
            "description": "Invalid channel ID or empty message",
        },
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {"model": ErrorResponse, "description": "Access denied"},
        404: {"model": ErrorResponse, "description": "Channel not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def send_channel_message(
    channel_id: str,
    body: MessageCreateRequest,
    current_user: TokenInfo = Depends(get_current_user),
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
        try:
            cid = int(channel_id)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid channel ID"}},
            )

        if not body.content and not body.attachments and not body.embeds:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": {
                        "code": 400,
                        "message": "Message must have content, attachments, or embeds",
                    }
                },
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
                    "hash": a.hash,
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
                            embeds=body.embeds,
                        )
                    else:
                        # Found server channel but it has no conversation_id
                        logger.warning(
                            f"Server channel {cid} has no conversation_id linked"
                        )
                        # Fall back to trying the channel ID as conversation ID (backward compatibility)
                        if messaging:
                            try:
                                msg = messaging.send_message(
                                    user_id=current_user.user_id,
                                    conversation_id=cid,
                                    content=body.content or "",
                                    reply_to_id=reply_to,
                                    attachments=attachments,
                                    embeds=body.embeds,
                                )
                            except Exception:
                                msg = None
            except Exception as e:
                exc_name = type(e).__name__
                if isinstance(e, AttachmentLimitError):
                    raise HTTPException(
                        status_code=400,
                        detail={"error": {"code": 400, "message": str(e)}},
                    )
                if "NotFound" not in exc_name:
                    if "Permission" in exc_name or "Access" in exc_name:
                        raise HTTPException(
                            status_code=403,
                            detail={"error": {"code": 403, "message": str(e)}},
                        )
                    elif "Content" in exc_name or "Invalid" in exc_name:
                        raise HTTPException(
                            status_code=400,
                            detail={"error": {"code": 400, "message": str(e)}},
                        )
                    # For other errors, log and potentially re-raise
                    logger.error(
                        f"Error sending message in server channel {cid}: {e}",
                        exc_info=True,
                    )
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
                    embeds=body.embeds,
                )
            except Exception as e:
                exc_name = type(e).__name__
                if isinstance(e, AttachmentLimitError):
                    raise HTTPException(
                        status_code=400,
                        detail={"error": {"code": 400, "message": str(e)}},
                    )
                if "NotFound" in exc_name or "Access" in exc_name:
                    raise HTTPException(
                        status_code=404,
                        detail={"error": {"code": 404, "message": "Channel not found"}},
                    )
                elif "Content" in exc_name or "Invalid" in exc_name:
                    raise HTTPException(
                        status_code=400,
                        detail={"error": {"code": 400, "message": str(e)}},
                    )
                logger.error(
                    f"Error sending message in channel {cid}: {e}", exc_info=True
                )
                raise

        if msg is None:
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": 404, "message": "Channel not found"}},
            )

        # Use username and avatar from token/auth - no need for extra DB lookup!
        author_username = current_user.username
        author_avatar_url = getattr(current_user, "avatar_url", None)
        author_badges = getattr(current_user, "badges", [])

        # If avatar or badges not in token, try to get from auth
        if not author_avatar_url or not author_badges:
            auth = api.get_auth()
            if auth:
                try:
                    user = auth.get_user(current_user.user_id)
                    if user:
                        if not author_avatar_url:
                            author_avatar_url = getattr(user, "avatar_url", None)
                        if not author_badges:
                            author_badges = getattr(user, "badges", [])
                except Exception:
                    pass

        response = _message_to_response(
            msg, 
            author_username, 
            author_avatar_url,
            author_badges=author_badges,
            channel_id=cid,
            media_mod=api.get_media()
        )

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
                        if server_id and servers_mod:
                            try:
                                user_ids = servers_mod.get_member_user_ids(server_id)
                            except Exception:
                                pass

                        if not user_ids and messaging:
                            try:
                                participants = messaging.get_participants(
                                    current_user.user_id, cid
                                )
                                user_ids = [p.user_id for p in (participants or [])]
                            except Exception:
                                pass

                        if user_ids:
                            event = Event(
                                event_type=EventType.MESSAGE_CREATE,
                                data=response.model_dump(),
                                server_id=server_id,  # Set for proper intent filtering
                                channel_id=cid,
                            )
                            await dispatcher.dispatch_event(event, user_ids)
                    except Exception as e:
                        logger.warning(f"Failed to broadcast MESSAGE_CREATE: {e}")

                # Schedule the broadcast task
                asyncio.create_task(broadcast_message())
        except Exception:
            pass

        return response
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Failed to send message to channel {channel_id}: {e}", exc_info=True
        )
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )


@router.get(
    "/channels/{channel_id}/messages/unread",
    response_model=UnreadCountResponse,
    summary="Get unread count",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid channel ID"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        404: {"model": ErrorResponse, "description": "Channel not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_unread_count(
    channel_id: str, current_user: TokenInfo = Depends(get_current_user)
) -> UnreadCountResponse:
    """Get unread message count for a channel."""
    messaging = api.get_messaging()
    if not messaging:
        raise HTTPException(
            status_code=500,
            detail={
                "error": {"code": 500, "message": "Messaging module not available"}
            },
        )

    try:
        try:
            cid = int(channel_id)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid channel ID"}},
            )

        counts = messaging.get_unread_count(current_user.user_id, cid)
        return UnreadCountResponse(
            channel_id=SnowflakeID(channel_id), unread_count=counts.get(cid, 0)
        )
    except HTTPException:
        raise
    except Exception as e:
        exc_name = type(e).__name__
        if "NotFound" in exc_name or "Access" in exc_name:
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": 404, "message": "Channel not found"}},
            )
        logger.error(
            f"Error getting unread count for channel {channel_id}: {e}", exc_info=True
        )
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )


@router.post(
    "/channels/{channel_id}/messages/ack",
    response_model=AckResponse,
    summary="Acknowledge messages",
    responses={
        400: {
            "model": ErrorResponse,
            "description": "Invalid channel ID or message ID",
        },
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        404: {"model": ErrorResponse, "description": "Channel not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def acknowledge_messages(
    channel_id: str,
    message_id: Optional[str] = Query(
        default=None, description="Mark as read up to this message ID"
    ),
    current_user: TokenInfo = Depends(get_current_user),
) -> AckResponse:
    """
    Mark messages as read in a channel (read receipts).

    If message_id is provided, marks all messages up to and including that message as read.
    If not provided, marks all messages in the channel as read.
    """
    messaging = api.get_messaging()
    servers_mod = api.get_servers()

    if not messaging:
        raise HTTPException(
            status_code=500,
            detail={
                "error": {"code": 500, "message": "Messaging module not available"}
            },
        )

    try:
        try:
            cid = int(channel_id)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid channel ID"}},
            )

        # Check if this is a voice channel - voice channels don't have messages to ack
        conv_id = cid # Default to channel ID (for DMs)
        is_server_channel = False
        if servers_mod:
            try:
                # Use a fast check first
                channel = servers_mod.get_channel(cid, current_user.user_id)
                if channel:
                    is_server_channel = True
                    # For server channels, use the linked conversation_id
                    if hasattr(channel, "conversation_id") and channel.conversation_id:
                        conv_id = channel.conversation_id
                    
                    channel_type = getattr(channel, "channel_type", None)
                    # Handle both enum and string types
                    channel_type_str = (
                        channel_type.value
                        if channel_type and hasattr(channel_type, "value")
                        else str(channel_type)
                    )
                    if channel_type_str in ("voice", "stage"):
                        # Voice channels don't have messages - return success with 0 marked
                        return AckResponse(success=True, messages_marked=0)
            except Exception:
                pass  # If we can't check, continue with normal flow

        up_to_id = int(message_id) if message_id else None

        try:
            from starlette.concurrency import run_in_threadpool
            # messaging.mark_read already validates access internally
            
            def _mark_read_with_cleanup(uid, cid, mid):
                import src.api as api
                db = api.get_db()
                try:
                    return messaging.mark_read(uid, cid, mid)
                finally:
                    if db:
                        db.close()

            count = await run_in_threadpool(_mark_read_with_cleanup, current_user.user_id, conv_id, up_to_id)
        except Exception as e:
            from src.core.messaging.exceptions import ConversationNotFoundError, ConversationAccessDeniedError
            if isinstance(e, ConversationNotFoundError):
                raise HTTPException(
                    status_code=404,
                    detail={"error": {"code": 404, "message": "Channel not found"}},
                )
            if isinstance(e, ConversationAccessDeniedError):
                raise HTTPException(
                    status_code=403,
                    detail={"error": {"code": 403, "message": "Access denied"}},
                )
            raise

        # Also mark notifications for this channel as read
        notif_mod = api.get_notifications()
        if notif_mod:
            try:
                notif_mod.mark_channel_read(current_user.user_id, cid)
            except Exception as ne:
                logger.debug(f"Failed to mark notifications read for channel {cid}: {ne}")

        # Broadcast read receipt event via WebSocket (fire and forget)
        import asyncio

        async def dispatch_ack():
            try:
                from src.api.websocket import get_dispatcher, is_setup as ws_is_setup
                from src.core.events.models import Event
                from src.core.events.types import EventType

                if ws_is_setup():
                    dispatcher = get_dispatcher()
                    user_ids = []
                    
                    if is_server_channel:
                        # For server channels, we notify all members in the channel
                        # This ensures their unread indicators/counts update live
                        try:
                            # In a real high-scale system, we might throttle this or use a different event
                            # for general "someone read" vs "you specifically got an ack", but for PlexiChat
                            # we broadcast it to all channel members to sync state.
                            user_ids = messaging.get_participant_ids(conv_id)
                        except Exception:
                            pass
                    else:
                        # For DMs/Groups, notify only other participants
                        try:
                            user_ids = messaging.get_participant_ids(conv_id)
                        except Exception:
                            pass
                    
                    # Always remove current user to avoid echoing back to sender
                    if user_ids:
                        user_ids = [uid for uid in user_ids if uid != current_user.user_id]
                    
                    if user_ids:
                        event = Event(
                            event_type=EventType.MESSAGE_ACK,
                            data={
                                "channel_id": str(cid),
                                "user_id": str(current_user.user_id),
                                "message_id": str(up_to_id) if up_to_id else None,
                            },
                        )
                        await dispatcher.dispatch_event(event, user_ids)
            except Exception as e:
                logger.debug(f"Failed to broadcast MESSAGE_ACK: {e}")

        if count > 0 or up_to_id:
            asyncio.create_task(dispatch_ack())

        return AckResponse(success=True, messages_marked=count)
    except HTTPException:
        raise
    except Exception as e:
        exc_name = type(e).__name__
        if "NotFound" in exc_name or "Access" in exc_name:
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": 404, "message": "Channel not found"}},
            )
        logger.error(
            f"Error acknowledging messages in channel {channel_id}: {e}", exc_info=True
        )
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )


@router.get(
    "/channels/{channel_id}/messages/{message_id}",
    response_model=MessageResponse,
    summary="Get message",
    responses={
        400: {
            "model": ErrorResponse,
            "description": "Invalid channel ID or message ID",
        },
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        404: {"model": ErrorResponse, "description": "Message not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_message(
    channel_id: str,
    message_id: str,
    current_user: TokenInfo = Depends(get_current_user),
) -> MessageResponse:
    """Get a specific message by ID."""
    messaging = api.get_messaging()
    if not messaging:
        raise HTTPException(
            status_code=500,
            detail={
                "error": {"code": 500, "message": "Messaging module not available"}
            },
        )

    try:
        try:
            cid = int(channel_id)
            mid = int(message_id)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid ID format"}},
            )

        message = messaging.get_message(current_user.user_id, mid)
        if not message:
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": 404, "message": "Message not found"}},
            )

        # Fetch author info for badges and username
        author_info = {"username": None, "avatar_url": None, "badges": []}
        auth = api.get_auth()
        if auth:
            try:
                user = auth.get_user(message.author_id)
                if user:
                    author_info["username"] = user.username
                    author_info["avatar_url"] = getattr(user, "avatar_url", None)
                    author_info["badges"] = getattr(user, "badges", [])
            except Exception:
                pass

        return _message_to_response(
            message, 
            author_username=author_info["username"],
            author_avatar_url=author_info["avatar_url"],
            author_badges=author_info["badges"],
            channel_id=cid, 
            media_mod=api.get_media()
        )
    except HTTPException:
        raise
    except Exception as e:
        exc_name = type(e).__name__
        if "NotFound" in exc_name:
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": 404, "message": "Message not found"}},
            )
        elif "Access" in exc_name:
            raise HTTPException(
                status_code=403,
                detail={"error": {"code": 403, "message": "Access denied"}},
            )
        logger.error(
            f"Error getting message {message_id} in channel {channel_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )


@router.patch(
    "/channels/{channel_id}/messages/{message_id}",
    response_model=MessageResponse,
    summary="Edit message",
    responses={
        400: {
            "model": ErrorResponse,
            "description": "Invalid message or channel ID or content",
        },
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {"model": ErrorResponse, "description": "Access denied"},
        404: {"model": ErrorResponse, "description": "Message not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def edit_message(
    channel_id: str,
    message_id: str,
    body: MessageUpdateRequest,
    current_user: TokenInfo = Depends(get_current_user),
) -> MessageResponse:
    """
    Edit a message.

    Updates the message content. Only the author can edit.
    """
    messaging = api.get_messaging()
    servers_mod = api.get_servers()
    auth = api.get_auth()

    if not messaging:
        raise HTTPException(
            status_code=500,
            detail={
                "error": {"code": 500, "message": "Messaging module not available"}
            },
        )

    try:
        try:
            mid = int(message_id)
            cid = int(channel_id)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": {"code": 400, "message": "Invalid message or channel ID"}
                },
            )

        msg = messaging.edit_message(current_user.user_id, mid, body.content)

        # Get author username and avatar
        author_username = current_user.username
        author_avatar_url = getattr(current_user, "avatar_url", None)
        author_badges = getattr(current_user, "badges", [])

        # If avatar or badges not in token, try to get from auth
        if not author_avatar_url or not author_badges:
            if auth:
                try:
                    user = auth.get_user(current_user.user_id)
                    if user:
                        if not author_avatar_url:
                            author_avatar_url = getattr(user, "avatar_url", None)
                        if not author_badges:
                            author_badges = getattr(user, "badges", [])
                except Exception:
                    pass

        response = _message_to_response(
            msg, 
            author_username, 
            author_avatar_url, 
            author_badges=author_badges,
            channel_id=cid,
            media_mod=api.get_media()
        )

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
                                    user_ids = servers_mod.get_member_user_ids(
                                        server_id
                                    )
                        except Exception:
                            pass

                    if not user_ids and messaging:
                        try:
                            participants = messaging.get_participants(
                                current_user.user_id, cid
                            )
                            user_ids = [p.user_id for p in (participants or [])]
                        except Exception:
                            pass

                    if user_ids:
                        # Determine server_id for intent filtering
                        event_server_id = None
                        if servers_mod:
                            try:
                                channel = servers_mod.get_channel(
                                    cid, current_user.user_id
                                )
                                if channel:
                                    event_server_id = getattr(
                                        channel, "server_id", None
                                    )
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
    except HTTPException:
        raise
    except Exception as e:
        exc_name = type(e).__name__
        if "NotFound" in exc_name:
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": 404, "message": "Message not found"}},
            )
        elif "Access" in exc_name or "Permission" in exc_name:
            raise HTTPException(
                status_code=403, detail={"error": {"code": 403, "message": str(e)}}
            )
        elif "Content" in exc_name:
            raise HTTPException(
                status_code=400, detail={"error": {"code": 400, "message": str(e)}}
            )
        logger.error(
            f"Error editing message {message_id} in channel {channel_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )


@router.delete(
    "/channels/{channel_id}/messages/{message_id}",
    response_model=SuccessResponse,
    summary="Delete message",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid message ID"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {"model": ErrorResponse, "description": "Access denied"},
        404: {"model": ErrorResponse, "description": "Message not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def delete_message(
    channel_id: str,
    message_id: str,
    current_user: TokenInfo = Depends(get_current_user),
) -> SuccessResponse:
    """
    Delete a message.

    Deletes the message. Author or moderators can delete.
    """
    messaging = api.get_messaging()
    if not messaging:
        raise HTTPException(
            status_code=500,
            detail={
                "error": {"code": 500, "message": "Messaging module not available"}
            },
        )

    try:
        try:
            mid = int(message_id)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid message ID"}},
            )

        # Get message details BEFORE deleting for broadcast
        msg = messaging.get_message(current_user.user_id, mid)
        if not msg:
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": 404, "message": "Message not found"}},
            )

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
                                    user_ids = servers_mod.get_member_user_ids(
                                        server_id
                                    )
                        except Exception:
                            pass

                    if not user_ids and messaging:
                        try:
                            participants = messaging.get_participants(
                                current_user.user_id, cid
                            )
                            user_ids = [p.user_id for p in (participants or [])]
                        except Exception:
                            pass

                    if user_ids:
                        event = Event(
                            event_type=EventType.MESSAGE_DELETE,
                            data={
                                "id": str(mid),
                                "channel_id": str(cid),
                                "server_id": str(server_id) if server_id else None,
                            },
                            server_id=server_id,
                            channel_id=cid,
                        )
                        await dispatcher.dispatch_event(event, user_ids)
            except Exception as e:
                logger.debug(f"Failed to broadcast MESSAGE_DELETE: {e}")

        asyncio.create_task(dispatch_delete())

        return SuccessResponse(success=True)
    except HTTPException:
        raise
    except Exception as e:
        exc_name = type(e).__name__
        if "NotFound" in exc_name:
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": 404, "message": "Message not found"}},
            )
        elif "Access" in exc_name or "Permission" in exc_name:
            raise HTTPException(
                status_code=403, detail={"error": {"code": 403, "message": str(e)}}
            )
        logger.error(
            f"Error deleting message {message_id} in channel {channel_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )


@router.get(
    "/channels/{channel_id}/pins",
    response_model=List[MessageResponse],
    summary="Get pinned messages",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid channel ID"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        404: {"model": ErrorResponse, "description": "Channel not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
@cached(ttl=120, prefix="channel_pins_api")
async def get_pinned_messages(
    channel_id: str, current_user: TokenInfo = Depends(get_current_user)
) -> List[MessageResponse]:
    """Get all pinned messages in a channel."""
    messaging = api.get_messaging()
    servers_mod = api.get_servers()
    auth = api.get_auth()

    try:
        try:
            cid = int(channel_id)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid channel ID"}},
            )

        messages = []

        # Try server channel first - get all messages and filter pinned
        if servers_mod:
            try:
                all_messages = servers_mod.get_channel_messages(
                    user_id=current_user.user_id, channel_id=cid, limit=500
                )
                if all_messages:
                    messages = [m for m in all_messages if getattr(m, "pinned", False)]
            except Exception:
                pass

        # If not a server channel, try DM conversation
        if not messages and messaging:
            try:
                messages = (
                    messaging.get_pinned_messages(current_user.user_id, cid) or []
                )
            except Exception:
                pass

        author_cache = {}  # {user_id: {"username": str, "avatar_url": str, "badges": list}}
        result = []
        for m in messages:
            author_id = m.author_id
            if author_id not in author_cache:
                author_info = {"username": None, "avatar_url": None, "badges": []}
                if auth:
                    try:
                        user = auth.get_user(author_id)
                        if user:
                            author_info["username"] = user.username
                            author_info["avatar_url"] = getattr(
                                user, "avatar_url", None
                            )
                            author_info["badges"] = getattr(user, "badges", [])
                    except Exception:
                        pass
                author_cache[author_id] = author_info
            info = author_cache.get(author_id, {})
            result.append(
                _message_to_response(
                    m, 
                    info.get("username"), 
                    info.get("avatar_url"),
                    author_badges=info.get("badges"),
                    media_mod=api.get_media()
                )
            )

        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get pinned messages for channel {channel_id}: {e}")
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )


@router.put(
    "/channels/{channel_id}/pins/{message_id}",
    response_model=SuccessResponse,
    summary="Pin message",
    responses={
        400: {
            "model": ErrorResponse,
            "description": "Invalid channel ID or message ID",
        },
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {"model": ErrorResponse, "description": "Access denied"},
        404: {"model": ErrorResponse, "description": "Message not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def pin_message(
    channel_id: str,
    message_id: str,
    current_user: TokenInfo = Depends(get_current_user),
) -> SuccessResponse:
    """Pin a message in a channel."""
    messaging = api.get_messaging()

    try:
        try:
            cid = int(channel_id)
            mid = int(message_id)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": {"code": 400, "message": "Invalid message or channel ID"}
                },
            )

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
                        from src.api.websocket import (
                            get_dispatcher,
                            is_setup as ws_is_setup,
                        )
                        from src.core.events.models import Event
                        from src.core.events.types import EventType

                        if ws_is_setup():
                            dispatcher = get_dispatcher()
                            servers_mod = api.get_servers()

                            user_ids = []
                            server_id = None
                            if servers_mod:
                                try:
                                    channel = servers_mod.get_channel(
                                        cid, current_user.user_id
                                    )
                                    if channel:
                                        server_id = getattr(channel, "server_id", None)
                                        if server_id:
                                            user_ids = servers_mod.get_member_user_ids(
                                                server_id
                                            )
                                except Exception:
                                    pass

                            if not user_ids and messaging:
                                try:
                                    participants = messaging.get_participants(
                                        current_user.user_id, cid
                                    )
                                    user_ids = [p.user_id for p in (participants or [])]
                                except Exception:
                                    pass

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

        return SuccessResponse(success=True)
    except HTTPException:
        raise
    except Exception as e:
        exc_name = type(e).__name__
        if "NotFound" in exc_name:
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": 404, "message": "Message not found"}},
            )
        elif "Permission" in exc_name:
            raise HTTPException(
                status_code=403, detail={"error": {"code": 403, "message": str(e)}}
            )
        logger.error(
            f"Error pinning message {message_id} in channel {channel_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )


@router.delete(
    "/channels/{channel_id}/pins/{message_id}",
    response_model=SuccessResponse,
    summary="Unpin message",
    responses={
        400: {
            "model": ErrorResponse,
            "description": "Invalid channel ID or message ID",
        },
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {"model": ErrorResponse, "description": "Access denied"},
        404: {"model": ErrorResponse, "description": "Message not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def unpin_message(
    channel_id: str,
    message_id: str,
    current_user: TokenInfo = Depends(get_current_user),
) -> SuccessResponse:
    """Unpin a message from a channel."""
    messaging = api.get_messaging()

    try:
        try:
            cid = int(channel_id)
            mid = int(message_id)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": {"code": 400, "message": "Invalid message or channel ID"}
                },
            )

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
                        from src.api.websocket import (
                            get_dispatcher,
                            is_setup as ws_is_setup,
                        )
                        from src.core.events.models import Event
                        from src.core.events.types import EventType

                        if ws_is_setup():
                            dispatcher = get_dispatcher()
                            servers_mod = api.get_servers()

                            user_ids = []
                            server_id = None
                            if servers_mod:
                                try:
                                    channel = servers_mod.get_channel(
                                        cid, current_user.user_id
                                    )
                                    if channel:
                                        server_id = getattr(channel, "server_id", None)
                                        if server_id:
                                            user_ids = servers_mod.get_member_user_ids(
                                                server_id
                                            )
                                except Exception:
                                    pass

                            if not user_ids and messaging:
                                try:
                                    participants = messaging.get_participants(
                                        current_user.user_id, cid
                                    )
                                    user_ids = [p.user_id for p in (participants or [])]
                                except Exception:
                                    pass

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

        return SuccessResponse(success=True)
    except HTTPException:
        raise
    except Exception as e:
        exc_name = type(e).__name__
        if "NotFound" in exc_name:
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": 404, "message": "Message not found"}},
            )
        elif "Permission" in exc_name:
            raise HTTPException(
                status_code=403, detail={"error": {"code": 403, "message": str(e)}}
            )
        logger.error(
            f"Error unpinning message {message_id} in channel {channel_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )


@router.get(
    "/users/@me/unread",
    response_model=AllUnreadCountsResponse,
    summary="Get all unread counts",
    responses={
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
@cached(ttl=10, prefix="unread_counts_api")
def get_all_unread_counts(
    current_user: TokenInfo = Depends(get_current_user),
) -> AllUnreadCountsResponse:
    """Get unread message counts for all conversations."""
    messaging = api.get_messaging()
    if not messaging:
        raise HTTPException(
            status_code=500,
            detail={
                "error": {"code": 500, "message": "Messaging module not available"}
            },
        )

    try:
        counts = messaging.get_unread_count(current_user.user_id)
        # Convert int keys to string for JSON
        return AllUnreadCountsResponse(
            unread_counts={str(k): v for k, v in counts.items()}
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Error getting all unread counts for user {current_user.user_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )


@router.post(
    "/channels/{channel_id}/typing",
    response_model=SuccessResponse,
    summary="Trigger typing indicator",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid channel ID"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        404: {"model": ErrorResponse, "description": "Channel not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def trigger_typing(
    channel_id: str, current_user: TokenInfo = Depends(get_current_user)
) -> SuccessResponse:
    """
    Trigger typing indicator in a channel.

    Broadcasts a typing event to other users in the channel.
    Works for both server channels and DM conversations.
    """
    import asyncio

    presence = api.get_presence()
    servers_mod = api.get_servers()
    messaging = api.get_messaging()

    try:
        try:
            cid = int(channel_id)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid channel ID"}},
            )

        channel = None
        user_ids = []

        # Try server channel first
        if servers_mod:
            try:
                channel = servers_mod.get_channel(cid, current_user.user_id)
                if channel:
                    # Check if this is a voice channel - voice channels don't have typing indicators
                    channel_type = getattr(channel, "channel_type", None)
                    channel_type_str = (
                        channel_type.value
                        if channel_type and hasattr(channel_type, "value")
                        else str(channel_type)
                    )
                    if channel_type_str in ("voice", "stage"):
                        # Voice channels don't support typing - return success silently
                        return SuccessResponse(success=True)

                    server_id = getattr(channel, "server_id", None)
                    if server_id:
                        # Use optimized function that only fetches user IDs
                        user_ids = servers_mod.get_member_user_ids(
                            server_id, exclude_user_id=current_user.user_id
                        )
            except Exception:
                channel = None

        # If not a server channel, try as DM conversation
        if not channel and messaging:
            try:
                participants = messaging.get_participants(current_user.user_id, cid)
                if participants:
                    user_ids = [
                        p.user_id
                        for p in participants
                        if p.user_id != current_user.user_id
                    ]
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
            # Capture username from token - no extra DB lookup needed!
            username = current_user.username

            async def dispatch_typing():
                try:
                    from src.api.websocket import (
                        get_dispatcher,
                        is_setup as ws_is_setup,
                    )
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
                                "username": username,
                            },
                            server_id=event_server_id,
                            channel_id=cid,
                        )
                        await dispatcher.dispatch_event(event, user_ids)
                except Exception as e:
                    logger.debug(f"Failed to dispatch typing event: {e}")

            # Fire and forget - don't wait for dispatch to complete
            asyncio.create_task(dispatch_typing())

        return SuccessResponse(success=True)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Failed to trigger typing indicator in channel {channel_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )

