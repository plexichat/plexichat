"""
Core message CRUD routes - Create, read, update, delete individual messages.
"""

from typing import Optional, Any
from fastapi import APIRouter, HTTPException, Depends
from starlette.concurrency import run_in_threadpool

import src.api as api
import utils.logger as logger
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.messages import (
    MessageCreateRequest,
    MessageUpdateRequest,
    MessageResponse,
)
from src.api.schemas.common import ErrorResponse, SuccessResponse
from src.core.messaging.exceptions import AttachmentLimitError, MessageNotFoundError
from src.core.servers.exceptions import (
    ServerNotFoundError,
    ChannelNotFoundError,
    ChannelAccessDeniedError,
    PermissionDeniedError,
)
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
)
from .helpers import _message_to_response
from .messages import get_msg_id

router = APIRouter(tags=["Messages"])


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

        # Try to send message - first try server channel, then fall back to DM
        msg, server_id = await _send_message_with_fallback(
            cid,
            current_user.user_id,
            content_value,
            reply_to,
            attachments,
            body.embeds,
            servers_mod,
            messaging,
        )

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
                    message_id=get_msg_id(msg),
                    context={"source": "message_create"},
                )
                if not result.passed:
                    for match in result.violations:
                        automod.process_violation(
                            server_id=server_id,
                            channel_id=cid,
                            user_id=current_user.user_id,
                            message_id=get_msg_id(msg),
                            match=match,
                            actions=result.actions_to_take,
                            context={"source": "message_create"},
                        )

                    if result.should_delete:
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
                if isinstance(
                    e, (MessageNotFoundError, ServerNotFoundError, ChannelNotFoundError)
                ):
                    raise HTTPException(
                        status_code=404,
                        detail={"error": {"code": 404, "message": str(e)}},
                    )
                if isinstance(e, (PermissionDeniedError, ChannelAccessDeniedError)):
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
                            get_msg_id(msg),
                            hard_delete=True,
                        )
                    except Exception:
                        pass
                logger.error(
                    f"Error creating poll for message {get_msg_id(msg)}: {e}",
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

        message = await run_in_threadpool(
            messaging.get_message, current_user.user_id, mid
        )
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
                user = await run_in_threadpool(auth.get_user, message.author_id)
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
        if isinstance(
            e, (MessageNotFoundError, ServerNotFoundError, ChannelNotFoundError)
        ):
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": 404, "message": "Message not found"}},
            )
        elif isinstance(e, (ChannelAccessDeniedError, PermissionDeniedError)):
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

        # Fetch channel once for timeout check + server_id lookup (avoid double fetch)
        server_id = None
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
                if (
                    isinstance(e, HTTPException)
                    and getattr(e, "status_code", None) == 403
                ):
                    raise
                server_id = None

        msg = messaging.edit_message(current_user.user_id, mid, body.content)

        if server_id:
            try:
                from src.core import automod

                content = getattr(msg, "content", None) or body.content or ""
                result = automod.check_message(
                    server_id=server_id,
                    channel_id=cid,
                    user_id=current_user.user_id,
                    content=content,
                    message_id=get_msg_id(msg),
                    context={"source": "message_edit"},
                )
                if not result.passed:
                    for match in result.violations:
                        automod.process_violation(
                            server_id=server_id,
                            channel_id=cid,
                            user_id=current_user.user_id,
                            message_id=get_msg_id(msg),
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
        if isinstance(
            e, (MessageNotFoundError, ServerNotFoundError, ChannelNotFoundError)
        ):
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": 404, "message": "Message not found"}},
            )
        elif isinstance(e, (ChannelAccessDeniedError, PermissionDeniedError)):
            raise HTTPException(
                status_code=403, detail={"error": {"code": 403, "message": str(e)}}
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

        return SuccessResponse(success=True, message=None)
    except HTTPException:
        raise
    except Exception as e:
        if isinstance(
            e, (MessageNotFoundError, ServerNotFoundError, ChannelNotFoundError)
        ):
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": 404, "message": "Message not found"}},
            )
        elif isinstance(e, (ChannelAccessDeniedError, PermissionDeniedError)):
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


async def _send_message_with_fallback(
    channel_id: int,
    user_id: int,
    content: str,
    reply_to_id: Optional[int],
    attachments: Optional[list],
    embeds: Optional[list],
    servers_mod: Any,
    messaging: Any,
) -> tuple:
    """
    Send a message with fallback logic: try server channel first, then DM conversation.

    Returns:
        Tuple of (message, server_id)
    """
    msg = None
    server_id = None

    # Try server channel first
    if servers_mod:
        try:
            channel = servers_mod.get_channel(channel_id, user_id)
            if channel:
                server_id = getattr(channel, "server_id", None)
                conversation_id = getattr(channel, "conversation_id", None)

                # Check timeout
                if server_id and hasattr(servers_mod, "is_timed_out"):
                    if servers_mod.is_timed_out(user_id, server_id):
                        raise HTTPException(
                            status_code=403,
                            detail={
                                "error": {
                                    "code": 403,
                                    "message": "You are currently timed out in this server",
                                }
                            },
                        )

                # Send with conversation_id if available
                if conversation_id and messaging:
                    msg = await run_in_threadpool(
                        messaging.send_message,
                        user_id=user_id,
                        conversation_id=conversation_id,
                        content=content,
                        reply_to_id=reply_to_id,
                        attachments=attachments,
                        embeds=embeds,
                    )
                elif messaging:
                    # Fallback: use channel_id as conversation_id (backward compatibility)
                    logger.warning(
                        f"Server channel {channel_id} has no conversation_id linked"
                    )
                    try:
                        msg = await run_in_threadpool(
                            messaging.send_message,
                            user_id=user_id,
                            conversation_id=channel_id,
                            content=content,
                            reply_to_id=reply_to_id,
                            attachments=attachments,
                            embeds=embeds,
                        )
                    except Exception:
                        msg = None
        except HTTPException:
            raise
        except Exception as e:
            if isinstance(e, AttachmentLimitError):
                raise HTTPException(
                    status_code=400,
                    detail={"error": {"code": 400, "message": str(e)}},
                )
            if not isinstance(e, (ServerNotFoundError, ChannelNotFoundError)):
                if isinstance(e, (ChannelAccessDeniedError, PermissionDeniedError)):
                    raise HTTPException(
                        status_code=403,
                        detail={"error": {"code": 403, "message": str(e)}},
                    )
                # For other errors, log and potentially re-raise
                logger.error(
                    f"Error sending message in server channel {channel_id}: {e}",
                    exc_info=True,
                )
                raise
            # Channel not found in servers, fall through to try as DM conversation
            msg = None

    # Fallback: try as DM conversation
    if msg is None and messaging:
        try:
            msg = messaging.send_message(
                user_id=user_id,
                conversation_id=channel_id,
                content=content,
                reply_to_id=reply_to_id,
                attachments=attachments,
                embeds=embeds,
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
                f"Error sending message in channel {channel_id}: {e}", exc_info=True
            )
            raise

    return msg, server_id
