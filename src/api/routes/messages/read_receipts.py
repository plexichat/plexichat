"""
Read receipt routes - Message acknowledgment and unread counts.
"""

from typing import Optional, Dict
from fastapi import APIRouter, HTTPException, Depends, Query
from starlette.concurrency import run_in_threadpool

import src.api as api
import utils.logger as logger
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.messages import (
    UnreadCountResponse,
    AllUnreadCountsResponse,
    AckResponse,
)
from src.api.schemas.common import SnowflakeID, ErrorResponse, SuccessResponse
from src.core.database import cached
from src.core.servers.exceptions import (
    ServerNotFoundError,
    ChannelNotFoundError,
    ChannelAccessDeniedError,
    PermissionDeniedError,
)

router = APIRouter(tags=["Messages"])


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

        # Resolve channel_id to conversation_id for server channels
        conv_id = cid
        if servers_mod:
            try:
                channel = servers_mod.get_channel(cid, current_user.user_id)
                if (
                    channel
                    and hasattr(channel, "conversation_id")
                    and channel.conversation_id
                ):
                    conv_id = channel.conversation_id
            except Exception:
                pass

        counts = await run_in_threadpool(
            messaging.get_unread_count, current_user.user_id, conv_id
        )
        return UnreadCountResponse(
            channel_id=SnowflakeID(channel_id), unread_count=counts.get(cid, 0)
        )
    except HTTPException:
        raise
    except Exception as e:
        if isinstance(
            e,
            (
                ServerNotFoundError,
                ChannelNotFoundError,
                ChannelAccessDeniedError,
                PermissionDeniedError,
            ),
        ):
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
        if isinstance(
            e,
            (
                ServerNotFoundError,
                ChannelNotFoundError,
                ChannelAccessDeniedError,
                PermissionDeniedError,
            ),
        ):
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

        return SuccessResponse(success=True, message=None)
    except Exception as e:
        logger.error(f"Bulk ACK failed: {e}", exc_info=True)
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
