"""
Typing indicator routes - Typing indicators for channels.
"""

from fastapi import APIRouter, HTTPException, Depends

import src.api as api
import utils.logger as logger
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.common import ErrorResponse, SuccessResponse

router = APIRouter(tags=["Messages"])


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
