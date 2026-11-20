"""
Typing indicator routes - Typing indicators for channels.
"""

from typing import List

from src.core.security.hardening import parse_id

from fastapi import APIRouter, HTTPException, Depends

import src.api as api
import utils.logger as logger
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.common import ErrorResponse, SuccessResponse

router = APIRouter(tags=["Messages"])


def _filter_blocked_dm_recipients(
    actor_id: int,
    user_ids: List[int],
    relationships=None,
) -> List[int]:
    """Drop DM recipients who have a block relationship with the actor.

    Typing events are published with an *explicit* recipient list, so the
    gateway router's DM block filter (which only applies to implicit
    routing) never sees them. Without this gate a blocked pair still
    receives each other's "is typing" frames in a shared DM, leaking
    presence across a block that ``send_message`` already enforces.

    ``get_all_blocked_ids`` is the authoritative two-way block set. A
    lookup failure fails closed: nobody receives the event rather than
    risking delivery to an unverified recipient. When no relationship
    source is wired at all, the audience is left unchanged (matching the
    gateway router's behaviour) so typing keeps working on deployments
    without the optional relationships module.
    """
    if not user_ids:
        return user_ids
    if relationships is None:
        from src.core import relationships as rel_module

        if not (
            getattr(rel_module, "_setup_complete", False)
            and getattr(rel_module, "_manager", None) is not None
        ):
            return user_ids
        relationships = rel_module
    try:
        blocked_ids = {int(x) for x in relationships.get_all_blocked_ids(actor_id)}
    except Exception as e:
        logger.debug(f"typing DM block lookup failed: {e}")
        return []
    return [uid for uid in user_ids if int(uid) not in blocked_ids]


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
            cid = parse_id(channel_id, "channel_id")
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid channel ID"}},
            )

        channel = None
        user_ids = []
        is_server_channel = False

        # Try server channel first
        if servers_mod:
            try:
                channel = servers_mod.get_channel(cid, current_user.user_id)
            except Exception:
                channel = None
            if channel is not None:
                is_server_channel = True
                # Check if this is a voice channel - voice channels don't have typing indicators
                channel_type = getattr(channel, "channel_type", None)
                channel_type_str = (
                    channel_type.value
                    if channel_type and hasattr(channel_type, "value")
                    else str(channel_type)
                )
                if channel_type_str in ("voice", "stage"):
                    # Voice channels don't support typing - return success silently
                    return SuccessResponse(success=True, message=None)

                server_id = getattr(channel, "server_id", None)
                if server_id:
                    # Use channel-access-filtered recipients to avoid leaking
                    # typing indicators to users who can't view the channel.
                    # A failure here must leave the audience EMPTY, not fall
                    # through to the unfiltered DM participant list below.
                    from starlette.concurrency import run_in_threadpool
                    from src.api.websocket.handlers.presence import (
                        _get_channel_recipients,
                    )

                    try:
                        user_ids, _ = await run_in_threadpool(
                            _get_channel_recipients, current_user.user_id, cid
                        )
                    except Exception as recipient_error:
                        logger.debug(
                            f"typing channel audience lookup failed for {cid}: "
                            f"{recipient_error}"
                        )
                        user_ids = []

        if servers_mod and not is_server_channel:
            # ``get_channel`` also returns None when the actor lost access or
            # the lookup errored. Probe membership-agnostically before treating
            # the id as a DM: a server channel whose viewer-filtered audience
            # walk failed must broadcast to nobody, never to the raw
            # conversation participants (the exact audience leak the
            # channels.view filter exists to prevent).
            channel_exists = getattr(servers_mod, "channel_exists", None)
            if callable(channel_exists):
                try:
                    if channel_exists(cid):
                        is_server_channel = True
                except Exception:
                    # Cannot prove it is NOT a server channel -> fail closed.
                    is_server_channel = True

        # If not a server channel, try as DM conversation
        if not is_server_channel and messaging:
            try:
                participants = messaging.get_participants(current_user.user_id, cid)
                if participants:
                    user_ids = [
                        p.user_id
                        for p in participants
                        if p.user_id != current_user.user_id
                    ]
                    # The dispatcher takes an explicit audience, bypassing the
                    # router's DM block filter. Apply it here so a blocked pair
                    # never sees each other's typing frames.
                    user_ids = _filter_blocked_dm_recipients(
                        current_user.user_id, user_ids
                    )
            except Exception as participant_error:
                logger.debug(f"typing audience lookup failed: {participant_error}")

        # Set typing indicator in presence module — only for a channel the
        # user can actually access (CORRECTNESS FIX: typing was previously
        # recorded even for nonexistent / unauthorized channels).
        if presence and (channel or user_ids):
            try:
                presence.start_typing(current_user.user_id, cid)
            except Exception as typing_error:
                # Non-critical: the request still succeeds without it.
                logger.debug(f"start_typing failed: {typing_error}")

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

        return SuccessResponse(success=True, message=None)
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
