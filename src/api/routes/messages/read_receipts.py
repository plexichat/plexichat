"""
Read receipt routes - Message acknowledgment and unread counts.
"""

from src.core.security.hardening import parse_id

from typing import Optional, Dict
from fastapi import APIRouter, HTTPException, Depends, Query, Request
from starlette.concurrency import run_in_threadpool

from src.api.request_guards import read_body_capped

import src.api as api
import utils.logger as logger
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.routes.users.helpers import authenticated_cache_key, require_user_token
from src.api.schemas.messages import (
    UnreadCountResponse,
    AllUnreadCountsResponse,
    AckResponse,
    LastReadResponse,
    MarkUnreadRequest,
    MarkUnreadResponse,
    BulkReadResponse,
)
from src.api.schemas.common import SnowflakeID, ErrorResponse, SuccessResponse
from src.core.database import cached
from src.core.servers.exceptions import (
    ServerNotFoundError,
    ChannelNotFoundError,
    ChannelAccessDeniedError,
    PermissionDeniedError,
)
from .helpers import require_snowflake

router = APIRouter(tags=["Messages"])

# The bulk ACK body is a small id->cursor map (at most ``max_channels`` short
# entries). Without an endpoint-level cap, a crafted multi-megabyte JSON object
# is fully parsed and validated into a Python dict before the handler's entry
# count check can reject it. Cap the raw bytes first, the same way the channel
# PATCH route bounds its body.
MAX_BULK_ACK_BODY = 32 * 1024


async def _cap_bulk_ack_body(request: Request) -> None:
    """Reject oversized bulk-ACK bodies before Pydantic parses them."""
    await read_body_capped(request, MAX_BULK_ACK_BODY)


def _require_read_receipt_channel_view(cid: int, user_id: int, servers_mod) -> None:
    """Fail closed when ``cid`` is a server channel the caller cannot view.

    The read-receipt routes resolve the URL id to a conversation with a
    best-effort ``get_channel(cid, user)`` and otherwise treat the id as a DM
    conversation id. ``get_channel`` returns ``None`` for a genuine DM *and*
    for a channel denied by ``channels.view``; a server channel's linked
    ``conversation_id`` has no channel row at all. Without this gate a member
    denied view on a private channel can pass that linked id as the URL
    ``channel_id`` (an alias for the channel), skip the resolver, and reach the
    participation-only unread/read-state lookup -- server membership is seeded
    as participation regardless of ``channels.view``. Resolve both spellings
    membership-agnostically and require a live view grant on the real backing
    channel. Fails closed on any broken lookup; a genuine DM/group has no
    backing channel and is left to the messaging layer's participation gate.
    """
    def _deny() -> None:
        # Same 404 as a missing channel, so a restricted channel is not
        # disclosed and the alias cannot be used as an existence oracle.
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": 404, "message": "Channel not found"}},
        )

    if not servers_mod:
        # The backing-channel/alias lookup cannot be evaluated without the
        # servers module. A private-channel alias can exist in the DB
        # independently of that module, so skipping this gate would let a
        # member denied channels.view reach the participation-only
        # unread/read-state lookup. Deny instead; a wired store with no backing
        # channel still allows a genuine DM below.
        _deny()

    get_channel = getattr(servers_mod, "get_channel", None)
    channel_exists = getattr(servers_mod, "channel_exists", None)
    reverse = getattr(servers_mod, "get_channel_by_conversation", None)

    def _gate_alias() -> None:
        """Gate a linked conversation-id alias of a server channel."""
        if not callable(reverse):
            # Without the reverse lookup the alias cannot be checked; the
            # addressed-id gate above is all that is possible here.
            return
        try:
            backing = reverse(cid)
        except HTTPException:
            raise
        except Exception:
            _deny()
        if backing is None:
            return  # genuine DM/group: participation is the only boundary
        channel_id = (
            backing.get("id")
            if isinstance(backing, dict)
            else getattr(backing, "id", None)
        )
        if channel_id is None or not callable(get_channel):
            _deny()
        try:
            channel_id = int(channel_id)
        except (TypeError, ValueError):
            _deny()
        if channel_id == cid:
            return  # addressed-id gate already checked this exact channel
        try:
            if get_channel(channel_id, user_id) is None:
                _deny()
        except HTTPException:
            raise
        except Exception:
            _deny()

    if not callable(get_channel):
        # No view accessor: never treat a known channel id as a DM.
        if callable(channel_exists):
            try:
                if channel_exists(cid):
                    _deny()
            except HTTPException:
                raise
            except Exception:
                _deny()
        _gate_alias()
        return

    try:
        if get_channel(cid, user_id) is not None:
            return  # live view grant on the addressed id
    except HTTPException:
        raise
    except Exception:
        _deny()

    # No live view grant on the addressed id. It is either a view-denied
    # channel, a view-denied channel's linked-conversation alias, or a DM.
    if callable(channel_exists):
        try:
            if channel_exists(cid):
                _deny()
        except HTTPException:
            raise
        except Exception:
            _deny()  # cannot prove it is NOT a channel: fail closed
    _gate_alias()


def _filter_blocked_dm_ack_recipients(actor_id, user_ids, relationships=None):
    """Drop DM read-receipt recipients who have a block with the ACK actor.

    ``MESSAGE_ACK`` is published with an *explicit* recipient list, so the
    gateway router's DM block filter (which only applies to implicit routing)
    never sees it. Without this gate, a blocked pair in a shared DM/group still
    receives each other's read receipts, leaking read/presence activity across
    a block that ``send_message`` already enforces. This mirrors the typing
    route's ``_filter_blocked_dm_recipients`` and the shared gateway emitter's
    ``_dm_participants_without_blocks`` for the same ephemeral event family.

    ``get_all_blocked_ids`` is the authoritative two-way block set. A lookup
    failure fails closed: nobody receives the receipt rather than risking
    delivery to a blocked pair. When no relationship source is wired at all,
    the audience is left unchanged (matching the gateway router's behaviour).
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
        logger.debug(f"read receipt DM block lookup failed: {e}")
        return []
    return [uid for uid in user_ids if int(uid) not in blocked_ids]


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
            cid = parse_id(channel_id, "channel_id")
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid channel ID"}},
            )

        # A linked conversation id is accepted as an alias for its server
        # channel. Gate both spellings before the participation-only unread
        # lookup below (participation is every server member).
        _require_read_receipt_channel_view(cid, current_user.user_id, servers_mod)

        # Resolve channel_id to conversation_id for server channels. Only
        # "not a server channel" outcomes may fall back to treating the id as
        # a DM conversation id: swallowing access/permission errors here used
        # to fail OPEN and could route a blocked channel id into the unread
        # lookup. Those now propagate to the 404 mapping below.
        conv_id = cid
        if servers_mod:
            try:
                channel = servers_mod.get_channel(cid, current_user.user_id)
                if channel and getattr(channel, "conversation_id", None):
                    conv_id = channel.conversation_id
            except (ServerNotFoundError, ChannelNotFoundError) as channel_error:
                logger.debug(f"read receipt channel resolution failed: {channel_error}")

        counts = await run_in_threadpool(
            messaging.get_unread_count, current_user.user_id, conv_id
        )
        return UnreadCountResponse(
            channel_id=SnowflakeID(channel_id), unread_count=counts.get(conv_id, 0)
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
            cid = parse_id(channel_id, "channel_id")
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid channel ID"}},
            )

        # A linked conversation id is accepted as an alias for its server
        # channel. Gate both spellings before mark_read, which only checks
        # participation (every server member, regardless of channels.view).
        _require_read_receipt_channel_view(cid, current_user.user_id, servers_mod)

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
            except Exception as channel_error:
                # If the channel lookup fails, continue with the normal flow.
                logger.debug(
                    f"read receipt voice channel check failed: {channel_error}"
                )

        # ``message_id`` is a client-supplied optional cursor. Reuse the
        # shared range check so negatives and oversized snowflakes are
        # rejected with this route's 400 "Invalid message ID" contract before
        # they reach ``mark_read``.
        if message_id:
            up_to_id: Optional[int] = require_snowflake(
                message_id, "message_id", "Invalid message ID"
            )
        else:
            up_to_id = None

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
                        # Scope to members who can actually VIEW the channel —
                        # a raw server-member list would leak read receipts in
                        # restricted/private channels. The resolver fails closed.
                        try:
                            from src.core.events.gateway_emit import (
                                resolve_channel_event_recipients,
                            )

                            sid = int(current_server_id) if current_server_id else None
                            user_ids = resolve_channel_event_recipients(
                                cid, guild_id=sid
                            )
                            # The resolver scopes server channels by channels.view
                            # only; a blocked pair can still share a server. The
                            # dispatcher takes this explicit audience, so the
                            # router's DM block gate never sees it -- apply the
                            # same two-way gate as the DM path here. A failed
                            # lookup fails closed (empty audience).
                            user_ids = _filter_blocked_dm_ack_recipients(
                                current_user.user_id, user_ids
                            )
                            logger.debug(
                                f"ACK: Server channel {cid} in server {sid} scoped to {len(user_ids)} viewers"
                            )
                        except Exception as e:
                            logger.debug(
                                f"Failed to scope ACK recipients for channel {cid}: {e}"
                            )
                            user_ids = []
                    else:
                        # For DMs/Groups, messaging module is correct
                        try:
                            # Use the manager instance to get participant IDs
                            messaging_instance = msg_manager.get_manager()
                            user_ids = messaging_instance.get_participant_ids(cid)
                            # MESSAGE_ACK is dispatched with an explicit audience,
                            # bypassing the router's DM block filter. Apply the
                            # two-way block gate here so a blocked pair cannot
                            # observe each other's read receipts in a shared DM.
                            user_ids = _filter_blocked_dm_ack_recipients(
                                current_user.user_id, user_ids
                            )
                            logger.debug(
                                f"ACK: DM/Group channel {cid} has {len(user_ids)} participants"
                            )
                        except Exception as e:
                            logger.debug(
                                f"Failed to get messaging participant IDs for ACK: {e}"
                            )
                            # Fail closed: an unresolvable block set must never
                            # widen the audience to a possibly blocked pair.
                            user_ids = []

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


async def _dispatch_message_ack_broadcast(
    *,
    channel_id: int,
    message_id: Optional[int],
    current_user_id: int,
) -> None:
    """Fan out a MESSAGE_ACK event for a channel after read tracking.

    Shared by the single-channel ack and the bulk ack paths so every route
    that marks messages read broadcasts the real-time event other
    participants rely on for live read checkmarks (B4). Best-effort: any
    failure to resolve targets or dispatch is logged and never breaks the
    ack response.
    """
    try:
        from src.api.websocket import get_dispatcher, is_setup as ws_is_setup
        from src.core.events.models import Event
        from src.core.events.types import EventType

        if not ws_is_setup():
            return
        dispatcher = get_dispatcher()

        msg_manager = api.get_messaging()
        servers_mod = api.get_servers()
        user_ids = []
        is_server_channel = False
        server_id = None
        if servers_mod:
            try:
                channel = servers_mod.get_channel(channel_id, current_user_id)
                if channel:
                    is_server_channel = True
                    server_id = getattr(channel, "server_id", None)
            except Exception as channel_error:
                # Fall through to the participant lookup below.
                logger.debug(
                    f"read receipt server channel check failed: {channel_error}"
                )
        try:
            if is_server_channel and server_id is not None:
                # Scope to channel viewers only — a raw server-member list
                # leaks read receipts in restricted/private channels.
                from src.core.events.gateway_emit import (
                    resolve_channel_event_recipients,
                )

                user_ids = resolve_channel_event_recipients(
                    channel_id, guild_id=int(server_id)
                )
            elif msg_manager is not None:
                user_ids = msg_manager.get_manager().get_participant_ids(channel_id)

            # Both branches feed an explicit-audience dispatch, so the router's
            # DM block filter never sees either audience. Apply the two-way gate
            # to BOTH the DM participants and the server-channel viewers: a
            # blocked pair can share a server, and the single-channel route
            # already withholds receipts from them. Fails closed on lookup error.
            if user_ids:
                user_ids = _filter_blocked_dm_ack_recipients(current_user_id, user_ids)
        except Exception as e:
            logger.debug(
                f"Bulk ACK: failed to resolve MESSAGE_ACK targets for {channel_id}: {e}"
            )
            return

        user_ids = [uid for uid in user_ids if int(uid) != int(current_user_id)]
        if not user_ids:
            return

        event = Event(
            event_type=EventType.MESSAGE_ACK,
            data={
                "channel_id": str(channel_id),
                "user_id": str(current_user_id),
                "message_id": str(message_id) if message_id is not None else None,
            },
        )
        await dispatcher.dispatch_event(event, user_ids)
        logger.debug(
            f"Bulk ACK: broadcast MESSAGE_ACK for channel {channel_id} "
            f"to {len(user_ids)} users"
        )
    except Exception as e:
        logger.error(
            f"Bulk ACK: failed to broadcast MESSAGE_ACK for channel {channel_id}: {e}",
            exc_info=True,
        )


@router.post(
    "/channels/ack/bulk",
    response_model=SuccessResponse,
    summary="Bulk acknowledge messages",
    dependencies=[Depends(_cap_bulk_ack_body)],
    responses={
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        413: {"model": ErrorResponse, "description": "Payload too large"},
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

    max_channels = 100
    if len(body) > max_channels:
        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "code": 400,
                    "message": f"Too many channels (max {max_channels})",
                }
            },
        )

    try:
        import asyncio

        # Validate the whole bulk map before any mutation: a negative or
        # oversized channel/message id is rejected up front instead of being
        # passed to mark_read and silently swallowed by the per-entry handler.
        parsed_ids = {}
        for channel_id, message_id in body.items():
            cid = require_snowflake(channel_id, "channel_id", "Invalid channel ID")
            mid = (
                require_snowflake(message_id, "message_id", "Invalid message ID")
                if message_id
                else None
            )
            parsed_ids[cid] = mid

        # The bulk path must not be a bypass of the single-channel view gate:
        # a linked conversation id accepted as a channel-id alias would reach
        # mark_read_bulk with only the participation check (every member).
        servers_mod = api.get_servers()
        for cid in parsed_ids:
            _require_read_receipt_channel_view(cid, current_user.user_id, servers_mod)

        # BEFORE: one mark_read() per channel — each ran its own participant,
        # MAX(id), settings, SELECT and executemany round trips. AFTER: a
        # single bulk call resolves and writes all channels with bounded
        # chunking; the loop below only handles best-effort side effects.
        try:
            marked_by_channel = await run_in_threadpool(
                msg_manager.get_manager().mark_read_bulk,
                current_user.user_id,
                list(parsed_ids.items()),
            )
        except Exception as e:
            logger.error(f"Bulk ACK: batched mark_read failed: {e}", exc_info=True)
            raise

        for cid, mid in parsed_ids.items():
            if not marked_by_channel.get(cid):
                continue
            try:
                # Mirror the single-channel ack: clear the channel's
                # notifications and broadcast MESSAGE_ACK so other
                # participants see live read checkmarks (B4).
                notif_mod = api.get_notifications()
                if notif_mod:
                    try:
                        notif_mod.mark_channel_read(current_user.user_id, cid)
                    except Exception as ne:
                        logger.debug(
                            f"Bulk ACK: failed to mark notifications read for "
                            f"channel {cid}: {ne}"
                        )

                asyncio.create_task(
                    _dispatch_message_ack_broadcast(
                        channel_id=cid,
                        message_id=mid,
                        current_user_id=current_user.user_id,
                    )
                )
            except Exception as e:
                logger.warning(f"Bulk ACK: Failed for channel {cid}: {e}")

        return SuccessResponse(success=True, message=None)
    except HTTPException:
        raise
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
@cached(
    ttl=10,
    prefix="unread_counts_api",
    key_builder=authenticated_cache_key("unread_counts_api"),
    l1_ttl=2,
)
def get_all_unread_counts(
    current_user: TokenInfo = Depends(get_current_user),
) -> AllUnreadCountsResponse:
    """Get unread message counts for all conversations."""
    require_user_token(current_user)
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


@router.get(
    "/channels/{channel_id}/messages/last-read",
    response_model=LastReadResponse,
    summary="Get last read position",
    responses={
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {"model": ErrorResponse, "description": "Access denied"},
        404: {"model": ErrorResponse, "description": "Channel not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_last_read(
    channel_id: str,
    current_user: TokenInfo = Depends(get_current_user),
) -> LastReadResponse:
    """Get the last read message position for the current user in a channel."""
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
        cid = parse_id(channel_id, "channel_id")
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Invalid channel ID"}},
        )

    # A linked conversation id is an alias for its server channel; gate both
    # spellings before the participation-only last-read lookup below.
    _require_read_receipt_channel_view(cid, current_user.user_id, servers_mod)

    # Resolve to conversation_id
    conv_id = cid
    if servers_mod:
        try:
            channel = servers_mod.get_channel(cid, current_user.user_id)
            if channel:
                if hasattr(channel, "conversation_id") and channel.conversation_id:
                    conv_id = channel.conversation_id
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": 404, "message": "Channel not found"}},
            )

    try:
        from starlette.concurrency import run_in_threadpool

        def _get_last_read(uid, cid):
            from src.core import messaging

            return messaging.get_last_read(uid, cid)

        result = await run_in_threadpool(_get_last_read, current_user.user_id, conv_id)
        return LastReadResponse(
            channel_id=SnowflakeID(channel_id),
            last_read_message_id=result.get("last_read_message_id"),
            last_read_at=result.get("last_read_at"),
        )
    except Exception:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.post(
    "/channels/{channel_id}/messages/mark-unread",
    response_model=MarkUnreadResponse,
    summary="Mark messages as unread",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid channel ID"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {"model": ErrorResponse, "description": "Access denied"},
        404: {"model": ErrorResponse, "description": "Channel not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def mark_unread(
    channel_id: str,
    body: Optional[MarkUnreadRequest] = None,
    current_user: TokenInfo = Depends(get_current_user),
) -> MarkUnreadResponse:
    """Mark messages as unread in a channel."""
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
        cid = parse_id(channel_id, "channel_id")
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Invalid channel ID"}},
        )

    # A linked conversation id is an alias for its server channel; gate both
    # spellings before the participation-only mark-unread lookup below.
    _require_read_receipt_channel_view(cid, current_user.user_id, servers_mod)

    conv_id = cid
    if servers_mod:
        try:
            channel = servers_mod.get_channel(cid, current_user.user_id)
            if channel:
                if hasattr(channel, "conversation_id") and channel.conversation_id:
                    conv_id = channel.conversation_id
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": 404, "message": "Channel not found"}},
            )

    try:
        from starlette.concurrency import run_in_threadpool

        message_id = body.message_id if body else None

        def _mark_unread(uid, cid, mid):
            from src.core import messaging

            return messaging.mark_unread(uid, cid, mid)

        new_count = await run_in_threadpool(
            _mark_unread, current_user.user_id, conv_id, message_id
        )
        return MarkUnreadResponse(success=True, unread_count=new_count)
    except Exception as e:
        from src.core.messaging.exceptions import (
            ConversationAccessDeniedError as MsgConvDenied,
        )

        if isinstance(e, MsgConvDenied):
            raise HTTPException(
                status_code=403,
                detail={"error": {"code": 403, "message": "Access denied"}},
            )
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.post(
    "/servers/{server_id}/messages/read-all",
    response_model=BulkReadResponse,
    summary="Mark all channels in a server as read",
    responses={
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {"model": ErrorResponse, "description": "Access denied"},
        404: {"model": ErrorResponse, "description": "Server not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def mark_all_server_read(
    server_id: str,
    current_user: TokenInfo = Depends(get_current_user),
) -> BulkReadResponse:
    """Mark all channels in a server as read."""
    msg_manager = api.get_messaging()
    servers_mod = api.get_servers()

    if not msg_manager or not servers_mod:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Module not available"}},
        )

    sid = require_snowflake(server_id, "server_id", "Invalid server ID")

    try:
        from starlette.concurrency import run_in_threadpool

        def _mark_all_read(uid, sid):
            from src.core import messaging

            return messaging.mark_all_server_read(uid, sid)

        channels_marked = await run_in_threadpool(
            _mark_all_read, current_user.user_id, sid
        )
        return BulkReadResponse(success=True, channels_marked=channels_marked)
    except Exception as e:
        logger.error(
            f"Error marking all read for server {server_id}: {e}", exc_info=True
        )
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )
