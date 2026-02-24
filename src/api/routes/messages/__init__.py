"""
Message routes - Message CRUD endpoints.
"""

from typing import Optional, List, Dict
from fastapi import APIRouter, HTTPException, Depends, Query

import src.api as api
import utils.logger as logger
from src.core.database import cached
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.messages import (
    MessageCreateRequest,
    MessageUpdateRequest,
    MessageResponse,
    UnreadCountResponse,
    AllUnreadCountsResponse,
    AckResponse,
)
from src.api.schemas.common import SnowflakeID, ErrorResponse, SuccessResponse
from src.core.messaging.exceptions import AttachmentLimitError
from src.core.polls import (
    PollResultsVisibility,
    PollNotFoundError,
    PollOptionNotFoundError,
    PollEndedError,
    InvalidPollQuestionError,
    InvalidPollOptionError,
    PollOptionLimitError,
    InvalidPollDurationError,
    AlreadyVotedError,
    MultipleVoteNotAllowedError,
    PermissionDeniedError,
    MessageNotFoundError,
)
from .helpers import _message_to_response

router = APIRouter(tags=["Messages"])


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
        author_cache = {}  # {str(user_id): {"username": str, "avatar_url": str, "badges": list}}
        if auth and author_ids:
            try:
                users = auth.get_user_profiles_bulk(author_ids)
                # Ensure all keys in author_cache are strings for consistent lookup
                author_cache = {
                    str(uid): {
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
        readers_cache = {}  # {str(message_id): [username, ...]}
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
                    reader_ids_map = messaging.get_batch_reader_ids(
                        current_user.user_id, own_message_ids
                    )

                    # Collect all unique reader IDs to fetch usernames in bulk
                    all_reader_ids = set()
                    for r_ids in reader_ids_map.values():
                        all_reader_ids.update(r_ids)

                    if all_reader_ids:
                        reader_users = auth.get_user_profiles_bulk(list(all_reader_ids))
                        # Use string keys for robust lookup
                        reader_users_str = {
                            str(uid): u for uid, u in reader_users.items()
                        }

                        # Build the readers cache with ReaderInfo objects
                        for mid, r_ids in reader_ids_map.items():
                            readers_cache[str(mid)] = [
                                {
                                    "id": str(rid),
                                    "username": reader_users_str[str(rid)]["username"],
                                    "avatar_url": reader_users_str[str(rid)].get(
                                        "avatar_url"
                                    ),
                                }
                                for rid in r_ids
                                if str(rid) in reader_users_str
                            ]
            except Exception as e:
                logger.warning(f"Failed to bulk fetch reader info: {e}")

        result = []
        for m in messages:
            # Robust lookup using string keys
            author_id = getattr(m, "author_id", None) or m.get("author_id")
            mid = getattr(m, "id", None) or m.get("id")
            author_info = author_cache.get(str(author_id)) or {}

            result.append(
                _message_to_response(
                    m,
                    author_username=author_info.get("username"),
                    author_avatar_url=author_info.get("avatar_url"),
                    author_badges=author_info.get("badges"),
                    reactions_data=reactions_cache.get(mid, []),
                    read_by_data=readers_cache.get(str(mid)),
                    media_mod=media_mod,
                    viewer_user_id=current_user.user_id,
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
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
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

        # Use messaging module's search (handles encryption via blind index)
        if messaging:
            try:
                # messaging.search_messages handles both DMs and server channels
                # as they are all backed by the same conversation system
                messages = messaging.search_messages(
                    user_id=current_user.user_id,
                    conversation_id=cid,
                    query=content,
                    limit=limit,
                )
            except Exception as e:
                logger.debug(f"Messaging search failed: {e}")

        # Bulk fetch all author info
        author_ids = list(set(m.author_id for m in messages))
        author_cache = {}
        if auth and author_ids:
            try:
                users = auth.get_user_profiles_bulk(author_ids)
                author_cache = {
                    str(uid): {
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
            author_info = author_cache.get(str(author_id)) or {}
            result.append(
                _message_to_response(
                    m,
                    author_username=author_info.get("username"),
                    author_avatar_url=author_info.get("avatar_url"),
                    author_badges=author_info.get("badges"),
                    media_mod=media_mod,
                    viewer_user_id=current_user.user_id,
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
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
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

        if (
            not body.content
            and not body.attachments
            and not body.embeds
            and not body.poll
        ):
            raise HTTPException(
                status_code=400,
                detail={
                    "error": {
                        "code": 400,
                        "message": "Message must have content, attachments, embeds, or a poll",
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
                    "checksum": a.hash,
                    "metadata": a.metadata,
                }
                for a in body.attachments
            ]

        content_value = body.content or ""
        if (not content_value or not content_value.strip()) and (
            attachments or body.poll
        ):
            content_value = "\u200b"

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

                    # Check if user is timed out in this server
                    if server_id and hasattr(servers_mod, "is_timed_out"):
                        if servers_mod.is_timed_out(current_user.user_id, server_id):
                            raise HTTPException(
                                status_code=403,
                                detail={
                                    "error": {
                                        "code": 403,
                                        "message": "You are currently timed out in this server",
                                    }
                                },
                            )

                    # Send directly to messaging module using cached conversation_id
                    if conversation_id and messaging:
                        msg = messaging.send_message(
                            user_id=current_user.user_id,
                            conversation_id=conversation_id,
                            content=content_value,
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
                                    content=content_value,
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
                    content=content_value,
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

        if server_id:
            try:
                from src.core import automod

                content = getattr(msg, "content", None) or body.content or ""
                result = automod.check_message(
                    server_id=server_id,
                    channel_id=cid,
                    user_id=current_user.user_id,
                    content=content,
                    message_id=getattr(msg, "id", None),
                    context={"source": "message_create"},
                )
                if not result.passed:
                    for match in result.violations:
                        automod.process_violation(
                            server_id=server_id,
                            channel_id=cid,
                            user_id=current_user.user_id,
                            message_id=getattr(msg, "id", None),
                            match=match,
                            actions=result.actions_to_take,
                            context={"source": "message_create"},
                        )

                    if result.should_delete:
                        # If the message was already saved, we might want to hard delete it here
                        # but DeleteMessageAction already marks it as deleted in DB.
                        # We should raise an exception to prevent the client from thinking it succeeded normally
                        raise HTTPException(
                            status_code=400,
                            detail={
                                "error": {
                                    "code": "MESSAGE_BLOCKED",
                                    "message": "Message blocked by auto-moderation",
                                    "violations": [
                                        v.rule_type.value for v in result.violations
                                    ],
                                }
                            },
                        )
            except HTTPException:
                raise
            except Exception as e:
                logger.warning(f"Automod check failed for message create: {e}")

        # Helper to get message ID robustly
        def get_msg_id(m):
            if m is None:
                return None
            return getattr(m, "id", None) or (
                m.get("id") if isinstance(m, dict) else None
            )

        if body.poll:
            polls_module = api.get_polls()
            if not polls_module:
                if messaging:
                    try:
                        messaging.delete_message(
                            current_user.user_id,
                            get_msg_id(msg),
                            hard_delete=True,
                        )
                    except Exception:
                        pass
                raise HTTPException(
                    status_code=500,
                    detail={
                        "error": {"code": 500, "message": "Polls module not available"}
                    },
                )
            try:
                msg_id = get_msg_id(msg)
                poll = polls_module.create_poll(
                    user_id=current_user.user_id,
                    message_id=msg_id,
                    question=body.poll.question,
                    options=list(body.poll.options),
                    duration_hours=body.poll.duration_hours,
                    allow_multiple_choice=body.poll.allow_multiple_choice,
                    results_visibility=PollResultsVisibility(
                        body.poll.results_visibility
                    ),
                )
                if messaging and poll:
                    try:
                        msg = messaging.update_message_metadata(
                            msg_id, {"poll_id": poll.id}
                        )
                    except Exception:
                        pass
            except (
                PollNotFoundError,
                PollOptionNotFoundError,
                PollEndedError,
                InvalidPollQuestionError,
                InvalidPollOptionError,
                PollOptionLimitError,
                InvalidPollDurationError,
                AlreadyVotedError,
                MultipleVoteNotAllowedError,
                PermissionDeniedError,
                MessageNotFoundError,
            ) as e:
                if messaging:
                    try:
                        messaging.delete_message(
                            current_user.user_id,
                            get_msg_id(msg),
                            hard_delete=True,
                        )
                    except Exception:
                        pass
                exc_name = type(e).__name__
                if "NotFound" in exc_name:
                    raise HTTPException(
                        status_code=404,
                        detail={"error": {"code": 404, "message": str(e)}},
                    )
                if "Permission" in exc_name or "Access" in exc_name:
                    raise HTTPException(
                        status_code=403,
                        detail={"error": {"code": 403, "message": str(e)}},
                    )
                raise HTTPException(
                    status_code=400,
                    detail={"error": {"code": 400, "message": str(e)}},
                )
            except Exception as e:
                if messaging:
                    try:
                        messaging.delete_message(
                            current_user.user_id,
                            getattr(msg, "id", None),
                            hard_delete=True,
                        )
                    except Exception:
                        pass
                logger.error(
                    f"Error creating poll for message {getattr(msg, 'id', None)}: {e}",
                    exc_info=True,
                )
                raise HTTPException(
                    status_code=500,
                    detail={"error": {"code": 500, "message": "Internal server error"}},
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
            media_mod=api.get_media(),
            viewer_user_id=current_user.user_id,
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
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
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
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
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
    msg_manager = api.get_messaging()
    servers_mod = api.get_servers()

    if not msg_manager:
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
        conv_id = cid  # Default to channel ID (for DMs)
        is_server_channel = False
        current_server_id = None
        if servers_mod:
            try:
                # Use a fast check first
                channel = servers_mod.get_channel(cid, current_user.user_id)
                if channel:
                    is_server_channel = True
                    current_server_id = getattr(channel, "server_id", None)
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
                    # Use the module-level helper or the manager directly
                    from src.core import messaging

                    # Log settings for debugging
                    try:
                        settings = messaging.get_user_message_settings(uid)
                        logger.info(
                            f"ACK: User {uid} in channel {cid} (conv {conv_id}), read_receipts={settings.read_receipts_enabled}"
                        )
                    except Exception as se:
                        logger.debug(
                            f"ACK: Failed to fetch settings for user {uid}: {se}"
                        )

                    count = messaging.mark_read(uid, cid, mid)
                    logger.info(f"ACK: Marked {count} messages as read for user {uid}")
                    return count
                finally:
                    if db:
                        db.close()

            count = await run_in_threadpool(
                _mark_read_with_cleanup, current_user.user_id, conv_id, up_to_id
            )
        except Exception as e:
            from src.core.messaging.exceptions import (
                ConversationNotFoundError,
                ConversationAccessDeniedError,
            )

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
                logger.debug(
                    f"Failed to mark notifications read for channel {cid}: {ne}"
                )

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
                        # For server channels, we MUST use the servers module
                        try:
                            servers = api.get_servers()
                            if servers and current_server_id:
                                sid = int(current_server_id)
                                # Fetch all members of the server
                                user_ids = servers.get_member_user_ids(sid)
                                logger.debug(
                                    f"ACK: Server channel {cid} in server {sid} has {len(user_ids)} members"
                                )
                        except Exception as e:
                            logger.debug(
                                f"Failed to get server member IDs for ACK: {e}"
                            )
                    else:
                        # For DMs/Groups, messaging module is correct
                        try:
                            # Use the manager instance to get participant IDs
                            messaging_instance = msg_manager.get_manager()
                            user_ids = messaging_instance.get_participant_ids(cid)
                            logger.debug(
                                f"ACK: DM/Group channel {cid} has {len(user_ids)} participants"
                            )
                        except Exception as e:
                            logger.debug(
                                f"Failed to get messaging participant IDs for ACK: {e}"
                            )

                    # Always remove current user to avoid echoing back to sender
                    raw_count = len(user_ids)
                    if user_ids:
                        user_ids = [
                            uid
                            for uid in user_ids
                            if int(uid) != int(current_user.user_id)
                        ]

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
                        logger.debug(
                            f"Successfully broadcast MESSAGE_ACK for channel {cid} to {len(user_ids)} users (excluded self from {raw_count})"
                        )
                    else:
                        logger.debug(
                            f"No targets found for MESSAGE_ACK in channel {cid} (is_server={is_server_channel}, raw_count={raw_count}, my_id={current_user.user_id})"
                        )
            except Exception as e:
                logger.error(f"Failed to broadcast MESSAGE_ACK: {e}", exc_info=True)

        if count > 0:
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
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.post(
    "/channels/ack/bulk",
    response_model=SuccessResponse,
    summary="Bulk acknowledge messages",
    responses={
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def acknowledge_messages_bulk(
    body: Dict[str, Optional[str]],
    current_user: TokenInfo = Depends(get_current_user),
) -> SuccessResponse:
    """
    Mark messages as read in multiple channels at once.

    Accepts a dictionary mapping channel_id to optional message_id.
    """
    msg_manager = api.get_messaging()
    if not msg_manager:
        raise HTTPException(
            status_code=500,
            detail={
                "error": {"code": 500, "message": "Messaging module not available"}
            },
        )

    try:
        for channel_id, message_id in body.items():
            try:
                cid = int(channel_id)
                mid = int(message_id) if message_id else None
                msg_manager.mark_read(current_user.user_id, cid, mid)
            except Exception as e:
                logger.warning(f"Bulk ACK: Failed for channel {channel_id}: {e}")

        return SuccessResponse(success=True)
    except Exception as e:
        logger.error(f"Bulk ACK failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
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
            media_mod=api.get_media(),
            viewer_user_id=current_user.user_id,
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
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
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

        # Check for server timeout if this is a server channel
        if servers_mod:
            try:
                channel = servers_mod.get_channel(cid, current_user.user_id)
                if channel:
                    server_id = getattr(channel, "server_id", None)
                    if server_id and hasattr(servers_mod, "is_timed_out"):
                        if servers_mod.is_timed_out(current_user.user_id, server_id):
                            raise HTTPException(
                                status_code=403,
                                detail={
                                    "error": {
                                        "code": 403,
                                        "message": "You are currently timed out in this server",
                                    }
                                },
                            )
            except Exception as e:
                # If we can't check server/channel permissions, let the messaging module handle it
                # unless it was an explicit timeout check failure
                if isinstance(e, HTTPException) and e.status_code == 403:
                    raise

        msg = messaging.edit_message(current_user.user_id, mid, body.content)

        server_id = None
        if servers_mod:
            try:
                channel = servers_mod.get_channel(cid, current_user.user_id)
                if channel:
                    server_id = getattr(channel, "server_id", None)
            except Exception:
                server_id = None

        if server_id:
            try:
                from src.core import automod

                content = getattr(msg, "content", None) or body.content or ""
                result = automod.check_message(
                    server_id=server_id,
                    channel_id=cid,
                    user_id=current_user.user_id,
                    content=content,
                    message_id=getattr(msg, "id", None),
                    context={"source": "message_edit"},
                )
                if not result.passed:
                    for match in result.violations:
                        automod.process_violation(
                            server_id=server_id,
                            channel_id=cid,
                            user_id=current_user.user_id,
                            message_id=getattr(msg, "id", None),
                            match=match,
                            actions=result.actions_to_take,
                            context={"source": "message_edit"},
                        )
            except Exception as e:
                logger.warning(f"Automod check failed for message edit: {e}")

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
            media_mod=api.get_media(),
            viewer_user_id=current_user.user_id,
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
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
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

        metadata = getattr(msg, "metadata", None)
        if metadata and isinstance(metadata, str):
            try:
                import json

                metadata = json.loads(metadata)
            except Exception:
                metadata = None

        if isinstance(metadata, dict) and metadata.get("poll_id"):
            polls_module = api.get_polls()
            if polls_module:
                try:
                    polls_module.delete_poll(
                        current_user.user_id, int(metadata["poll_id"])
                    )
                except Exception:
                    pass

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

                    # Use channel_id from the request URL, which should be the actual server channel ID
                    actual_channel_id = int(channel_id)

                    if servers_mod:
                        try:
                            # Verify this is a server channel and get server_id
                            channel = servers_mod.get_channel(
                                actual_channel_id, current_user.user_id
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
                            # Fallback for DMs using conversation_id
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
                                "channel_id": str(actual_channel_id),
                                "server_id": str(server_id) if server_id else None,
                            },
                            server_id=server_id,
                            channel_id=actual_channel_id,
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
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
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
                    media_mod=api.get_media(),
                    viewer_user_id=current_user.user_id,
                )
            )

        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get pinned messages for channel {channel_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
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
                response = _message_to_response(
                    msg,
                    channel_id=cid,
                    viewer_user_id=current_user.user_id,
                )

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
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
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
                response = _message_to_response(
                    msg,
                    channel_id=cid,
                    viewer_user_id=current_user.user_id,
                )

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
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
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
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
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
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )
