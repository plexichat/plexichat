"""
Pin routes - Pin and unpin messages.
"""

from src.core.security.hardening import parse_id

from fastapi import APIRouter, HTTPException, Depends

import src.api as api
import utils.logger as logger
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.common import ErrorResponse, SuccessResponse
from src.core.messaging.exceptions import (
    MessageNotFoundError,
    ConversationAccessDeniedError,
)
from src.core.servers.exceptions import (
    ServerNotFoundError,
    ChannelNotFoundError,
    ChannelAccessDeniedError,
    PermissionDeniedError,
)
from .helpers import _message_to_response, require_snowflake
from .messages_crud.base import MessagesCRUDBase
from .messages_crud.broadcast import _resolve_recipients

router = APIRouter(tags=["Messages"])

# Reuse the exact channel-scope/membership guards the CRUD get/edit/delete
# routes use so pin/unpin cannot be redirected at a message in a foreign
# conversation by swapping the channel id in the URL.
_channel_guard = MessagesCRUDBase()


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
            cid = parse_id(channel_id, "channel_id")
            # Pin/unpin act on a caller-supplied message id: give it the same
            # snowflake range check as every other id so negatives and
            # oversized values cannot reach the storage lookup.
            mid = require_snowflake(message_id, "message_id", "Invalid message ID")
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": {"code": 400, "message": "Invalid message or channel ID"}
                },
            )

        if messaging:
            # SECURITY (IDOR / cross-channel leak): resolve the target message
            # FIRST and bind it to the addressed URL channel before mutating
            # anything. The channel id is attacker-controlled and used to label
            # and fan out the broadcast; without this check a member of channel
            # A could pin a message from private channel B by passing A's id,
            # leaking B's content into A. Mirrors get/edit/delete semantics:
            # a channel mismatch returns the same 404 as a missing message.
            target = messaging.get_message(current_user.user_id, mid)
            if not target:
                raise HTTPException(
                    status_code=404,
                    detail={"error": {"code": 404, "message": "Message not found"}},
                )
            scope = _channel_guard._resolve_channel_scope(
                cid, current_user.user_id, api.get_servers()
            )
            _channel_guard._ensure_message_in_channel(target, scope)
            real_cid = int(getattr(target, "conversation_id"))

            messaging.pin_message(current_user.user_id, mid)

            # Refetch after the write so the broadcast carries the fresh
            # pinned=true state; fall back to the pre-write row if it vanished.
            msg = messaging.get_message(current_user.user_id, mid) or target
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
                    from src.core.events import build_message_update

                    if ws_is_setup():
                        dispatcher = get_dispatcher()
                        # SECURITY: reuse the shared recipient resolver so the
                        # pinned message content only reaches members that can
                        # actually VIEW the channel (channels.view), never the
                        # entire server member list. DM/group recipients come
                        # from the message's REAL conversation (real_cid), not
                        # the URL id, so content cannot cross conversations.
                        user_ids, event_server_id = _resolve_recipients(
                            api.get_servers(),
                            messaging,
                            cid,
                            real_cid,
                            current_user.user_id,
                        )

                        if user_ids:
                            event = build_message_update(
                                response.model_dump(),
                                channel_id=cid,
                                server_id=event_server_id,
                            )
                            await dispatcher.dispatch_event(event, user_ids)
                except Exception as e:
                    logger.debug(f"Failed to broadcast pin update: {e}")

            asyncio.create_task(dispatch_pin())

            try:
                from src.core.events.gateway_emit import emit_channel_pins_update

                server_id = None
                servers_mod = api.get_servers()
                if servers_mod:
                    try:
                        chan = servers_mod.get_channel(cid, current_user.user_id)
                        server_id = getattr(chan, "server_id", None)
                    except Exception as channel_error:
                        logger.debug(f"pin server id lookup failed: {channel_error}")
                emit_channel_pins_update(
                    channel_id=cid, guild_id=server_id, pinned_message_ids=None
                )
            except Exception as ge:
                logger.debug(f"emit_channel_pins_update failed: {ge}")

        return SuccessResponse(success=True, message=None)
    except HTTPException:
        raise
    except Exception as e:
        if isinstance(
            e, (ServerNotFoundError, ChannelNotFoundError, MessageNotFoundError)
        ):
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": 404, "message": "Message not found"}},
            )
        elif isinstance(
            e,
            (
                ChannelAccessDeniedError,
                PermissionDeniedError,
                ConversationAccessDeniedError,
            ),
        ):
            raise HTTPException(
                status_code=403,
                detail={"error": {"code": 403, "message": "Access denied"}},
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
            cid = parse_id(channel_id, "channel_id")
            # Pin/unpin act on a caller-supplied message id: give it the same
            # snowflake range check as every other id so negatives and
            # oversized values cannot reach the storage lookup.
            mid = require_snowflake(message_id, "message_id", "Invalid message ID")
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": {"code": 400, "message": "Invalid message or channel ID"}
                },
            )

        if messaging:
            # Same bind-before-mutate guard as pin (and the get/edit/delete
            # routes): the message must belong to the addressed channel.
            target = messaging.get_message(current_user.user_id, mid)
            if not target:
                raise HTTPException(
                    status_code=404,
                    detail={"error": {"code": 404, "message": "Message not found"}},
                )
            scope = _channel_guard._resolve_channel_scope(
                cid, current_user.user_id, api.get_servers()
            )
            _channel_guard._ensure_message_in_channel(target, scope)
            real_cid = int(getattr(target, "conversation_id"))

            messaging.unpin_message(current_user.user_id, mid)

            msg = messaging.get_message(current_user.user_id, mid) or target
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
                    from src.core.events import build_message_update

                    if ws_is_setup():
                        dispatcher = get_dispatcher()
                        # channels.view-filtered recipients from the message's
                        # REAL conversation — never the whole member list.
                        user_ids, event_server_id = _resolve_recipients(
                            api.get_servers(),
                            messaging,
                            cid,
                            real_cid,
                            current_user.user_id,
                        )

                        if user_ids:
                            event = build_message_update(
                                response.model_dump(),
                                channel_id=cid,
                                server_id=event_server_id,
                            )
                            await dispatcher.dispatch_event(event, user_ids)
                except Exception as e:
                    logger.debug(f"Failed to broadcast unpin update: {e}")

            asyncio.create_task(dispatch_unpin())

            try:
                from src.core.events.gateway_emit import emit_channel_pins_update

                server_id = None
                servers_mod = api.get_servers()
                if servers_mod:
                    try:
                        chan = servers_mod.get_channel(cid, current_user.user_id)
                        server_id = getattr(chan, "server_id", None)
                    except Exception as channel_error:
                        logger.debug(f"pin server id lookup failed: {channel_error}")
                emit_channel_pins_update(
                    channel_id=cid, guild_id=server_id, pinned_message_ids=None
                )
            except Exception as ge:
                logger.debug(f"emit_channel_pins_update failed: {ge}")

        return SuccessResponse(success=True, message=None)
    except HTTPException:
        raise
    except Exception as e:
        if isinstance(
            e, (ServerNotFoundError, ChannelNotFoundError, MessageNotFoundError)
        ):
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": 404, "message": "Message not found"}},
            )
        elif isinstance(
            e,
            (
                ChannelAccessDeniedError,
                PermissionDeniedError,
                ConversationAccessDeniedError,
            ),
        ):
            raise HTTPException(
                status_code=403,
                detail={"error": {"code": 403, "message": "Access denied"}},
            )
        logger.error(
            f"Error unpinning message {message_id} in channel {channel_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )
