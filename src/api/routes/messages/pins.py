"""
Pin routes - Pin and unpin messages.
"""

from fastapi import APIRouter, HTTPException, Depends

import src.api as api
import utils.logger as logger
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.common import ErrorResponse, SuccessResponse
from src.core.servers.exceptions import (
    ServerNotFoundError,
    ChannelNotFoundError,
    ChannelAccessDeniedError,
    PermissionDeniedError,
)
from .helpers import _message_to_response

router = APIRouter(tags=["Messages"])


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

        return SuccessResponse(success=True, message=None)
    except HTTPException:
        raise
    except Exception as e:
        if isinstance(e, (ServerNotFoundError, ChannelNotFoundError)):
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": 404, "message": "Message not found"}},
            )
        elif isinstance(e, (ChannelAccessDeniedError, PermissionDeniedError)):
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

        return SuccessResponse(success=True, message=None)
    except HTTPException:
        raise
    except Exception as e:
        if isinstance(e, (ServerNotFoundError, ChannelNotFoundError)):
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": 404, "message": "Message not found"}},
            )
        elif isinstance(e, (ChannelAccessDeniedError, PermissionDeniedError)):
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
