"""
Reaction routes - Message reaction endpoints.
"""

from src.core.reactions.manager.bounds import validate_route_emoji
from src.core.security.hardening import SNOWFLAKE_MAX, bounded_int, parse_id

import asyncio

from typing import List, Optional
from fastapi import APIRouter, HTTPException, Depends, Query, Request

import src.api as api
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.routes.users.helpers import (
    authenticated_cache_key,
    require_user_token,
)
from src.api.schemas.reactions import ReactionResponse, ReactionUserResponse
from src.api.schemas.common import SnowflakeID, ErrorResponse, SuccessResponse
from src.core import ratelimit
from src.core.database import cached, invalidate_pattern
from src.core.reactions.exceptions import InvalidEmojiError, MessageNotFoundError
import utils.logger as logger

router = APIRouter(tags=["Reactions"])


# GET /channels/{id}/messages/{id}/reactions is the hottest read in this
# module: every message view hydrates the reaction rows, aggregates counts and
# the per-viewer ``me`` flag, then serializes the list. Memoize the serialized
# body for a short window. The key is partitioned per credential by
# ``authenticated_cache_key`` (bot tokens get their own namespace, so a bot and
# its owner never share an entry), and the caller re-checks message access on
# every request *before* the cache lookup, so a revoked membership can never be
# served a still-warm body. Every reaction mutation drops the whole namespace
# because counts are visible to all viewers of the message. The L1 copy is
# deliberately short-lived to bound cross-worker staleness after invalidation.
# Concurrent cold misses are coalesced (single-flight) so a burst of message
# views cannot stampede the reaction loader, and a just-expired local copy is
# served for at most ``_REACTION_READ_STALE_TTL`` more seconds while one refresh
# runs in the background (bounded stale-while-revalidate). Invalidation clears
# both the stale copy and any in-flight refresh.
_REACTION_READ_CACHE_TTL = 10
_REACTION_READ_L1_TTL = 2
_REACTION_READ_STALE_TTL = 3
_REACTION_READ_PREFIX = "reaction_read_api"


def _invalidate_reaction_read_cache() -> None:
    """Drop every cached body that can embed a reaction after a mutation.

    The single-message reaction body lives in the ``reaction_read_api``
    namespace, but reaction pills (emoji, count and the per-viewer ``me`` flag)
    are ALSO hydrated into the cached channel-message-list body. That page is
    memoized in the shared ``messages_api`` body cache and in the in-process
    render LRU. Invalidating only the reaction namespace left a window where a
    page cached before an add/remove kept serving a pill for a reaction that no
    longer existed -- and a ``me`` flag for the wrong state. Drop all of them
    so the next read is authoritative (fail closed).
    """
    invalidate_pattern(f"{_REACTION_READ_PREFIX}:*")
    invalidate_pattern("*messages_api:*")
    try:
        from src.api.routes.messages.render_cache import get_render_cache

        get_render_cache().clear()
    except Exception as e:
        # Invalidation is best-effort only for the in-process tier; the shared
        # body cache above is authoritative. Never let a render-cache failure
        # turn a successful reaction mutation into a 500.
        logger.debug(f"reaction mutation render-cache clear failed: {e}")


@cached(
    ttl=_REACTION_READ_CACHE_TTL,
    prefix=_REACTION_READ_PREFIX,
    key_builder=authenticated_cache_key(_REACTION_READ_PREFIX),
    l1_ttl=_REACTION_READ_L1_TTL,
    single_flight=True,
    stale_while_revalidate=True,
    stale_ttl=_REACTION_READ_STALE_TTL,
)
async def _load_reactions(*, message_id: int, current_user: TokenInfo) -> list[dict]:
    """Cold path for GET .../reactions: hydrate and serialize the pills.

    Returns JSON-safe dicts so a cache hit (which reconstructs dicts) and a
    miss are byte-for-byte identical after the caller re-validates the models.
    The credential is part of the key because both the ``me`` flag and the
    blocked-user filter depend on the authenticated viewer.
    """
    reactions = api.get_reactions()
    result = reactions.get_reactions(current_user.user_id, message_id)
    return [
        ReactionResponse(
            emoji=r.emoji,
            count=r.count,
            me=r.me,
            url=getattr(r, "url", None),
            is_custom=getattr(r, "is_custom", False),
            custom_emoji_id=getattr(r, "custom_emoji_id", None),
        ).model_dump(mode="json")
        for r in result.reactions
    ]


def _validated_emoji(emoji: str) -> str:
    """Decode, validate and normalize an emoji path segment.

    All emoji routes (add, remove, moderator wipe, reactor lookup) call this
    helper, so the same boundary check now applies to every one of them.
    """
    try:
        return validate_route_emoji(emoji)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Invalid emoji"}},
        ) from exc


def _validate_message_in_channel(reactions, mid: int, cid: int) -> int:
    """Verify the message actually lives in the URL's channel.

    Prevents writing a reaction via one channel while broadcasting an event
    that claims another (attacker-controlled) channel id. Returns the real
    channel id used for events.
    """
    try:
        context = reactions.resolve_message_channel(mid)
    except Exception:
        context = None

    if not context:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": 404, "message": "Message not found"}},
        )

    real_channel_id = context.get("channel_id")
    if real_channel_id is None or str(real_channel_id) != str(cid):
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": 404, "message": "Message not found"}},
        )
    return int(real_channel_id)


def _require_channel_view(reactions, user_id: int, mid: int, action: str) -> None:
    """Fail closed when the actor cannot VIEW the message's channel.

    The core manager gates reaction writes on participation (server membership)
    plus ``messages.add_reactions`` / ``messages.manage``, but treats view
    access (``messages.read``) as a separate bit. Without this gate a member
    denied view by a channel override -- or a moderator who cannot see the
    channel -- can still write reactions there. Resolve the channel
    membership-agnostically so a revoked viewer is denied even though they
    remain a server member. DMs have no server channel row and are left to the
    manager's own participant gate.
    """
    servers_mod = api.get_servers()
    if not servers_mod:
        # The reverse lookup cannot be evaluated without the servers module.
        # A private-channel conversation_id alias can exist in the DB
        # independently of that module, so deferring to the manager here would
        # let a member denied messages.read react in a private channel. Deny
        # instead; a wired store with no backing channel still allows genuine
        # DMs below.
        raise HTTPException(
            status_code=403,
            detail={
                "error": {
                    "code": 403,
                    "message": f"Cannot {action} this message",
                }
            },
        )
    if not hasattr(servers_mod, "get_channel_by_conversation"):
        # A wired manager stub without the reverse-lookup API has no channel
        # view bit to check; defer to the manager's own fail-closed checks as
        # for DMs. This is the pre-existing wired control, unchanged.
        return

    conv_id = reactions.get_conversation_id_from_message(mid)
    try:
        srv_channel = (
            servers_mod.get_channel_by_conversation(conv_id) if conv_id else None
        )
    except Exception as exc:
        # The servers module IS wired but the reverse lookup raised. We cannot
        # verify ``messages.read``, so fail closed instead of allowing the
        # write. Only a genuinely absent API above defers to the manager.
        raise HTTPException(
            status_code=403,
            detail={
                "error": {
                    "code": 403,
                    "message": f"Cannot {action} this message",
                }
            },
        ) from exc

    if srv_channel and not servers_mod.has_permission(
        user_id,
        srv_channel["server_id"],
        "messages.read",
        srv_channel["id"],
    ):
        raise HTTPException(
            status_code=403,
            detail={
                "error": {
                    "code": 403,
                    "message": f"Cannot {action} this message",
                }
            },
        )


async def _dispatch_reaction_event(
    event_type: str,
    user_id: int,
    message_id: int,
    channel_id: int,
    emoji: str,
) -> None:
    """Helper to dispatch reaction events via WebSocket.

    The acting user is excluded from the fan-out: REST callers already apply
    optimistic updates locally and must not receive an echo (double-count).
    """
    try:
        from src.api.websocket import get_dispatcher, is_setup as ws_is_setup
        from src.core.events.models import Event
        from src.core.events.types import EventType

        if not ws_is_setup():
            return

        dispatcher = get_dispatcher()
        reactions = api.get_reactions()

        if not reactions:
            return

        # Get the conversation ID from the message
        conversation_id = reactions.get_conversation_id_from_message(message_id)
        if not conversation_id:
            return

        # Get all participants in the conversation (including server members)
        participant_ids = reactions.get_participant_ids(conversation_id)

        if not participant_ids:
            return

        # Exclude the acting user so their optimistic update is not doubled.
        recipient_ids = [pid for pid in participant_ids if pid != user_id]

        # The dispatcher takes an explicit audience, so the EventRouter's DM
        # block filter never sees it. Apply the same two-way gate used by the
        # typing / read-receipt / DM-message fan-outs: a participant who blocked
        # the actor, or whom the actor blocked, must not receive the actor's
        # reaction events. The shared helper fails closed when the
        # authoritative block set cannot be resolved.
        try:
            from src.core.events.gateway_emit import (
                _dm_participants_without_blocks,
            )

            recipient_ids = _dm_participants_without_blocks(recipient_ids, user_id)
        except Exception as filter_error:
            logger.debug(f"Reaction DM block filter unavailable: {filter_error}")
            return

        if not recipient_ids:
            return

        evt_type = {
            "add": EventType.MESSAGE_REACTION_ADD,
            "remove": EventType.MESSAGE_REACTION_REMOVE,
            "remove_emoji": EventType.MESSAGE_REACTION_REMOVE_EMOJI,
        }[event_type]

        event = Event(
            event_type=evt_type,
            data={
                "user_id": str(user_id),
                "message_id": str(message_id),
                "channel_id": str(channel_id),
                "emoji": emoji,
            },
        )
        await dispatcher.dispatch_event(event, recipient_ids)

    except Exception as e:
        logger.debug(f"Failed to dispatch reaction event for message {message_id}: {e}")


@router.put(
    "/channels/{channel_id}/messages/{message_id}/reactions/{emoji:path}",
    response_model=SuccessResponse,
    summary="Add reaction",
    responses={
        400: {
            "model": ErrorResponse,
            "description": "Invalid message ID or channel ID or emoji",
        },
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {"model": ErrorResponse, "description": "Access denied"},
        404: {"model": ErrorResponse, "description": "Message not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
        503: {"model": ErrorResponse, "description": "Reactions module not available"},
    },
)
async def add_reaction(
    channel_id: str,
    message_id: str,
    emoji: str,
    request: Request,
    current_user: TokenInfo = Depends(get_current_user),
) -> SuccessResponse:
    """
    Add a reaction to a message.

    Adds the specified emoji reaction to the message.
    """
    # A bot token's ``user_id`` is its human owner. Reject bot credentials
    # BEFORE the rate-limit spend below charges the owner's
    # ``user:<owner>:route:PUT /reactions`` bucket (and lets a leaked bot
    # evade its own quota), and before any view/permission gate can reject.
    require_user_token(current_user)
    # Rate limit reactions: 10 per second per user
    rl_result = await ratelimit.acheck_rate_limit(
        user_id=current_user.user_id,
        route="PUT /reactions",
        is_internal=getattr(request.state, "is_internal", False),
    )
    if not rl_result.allowed:
        raise HTTPException(
            status_code=429,
            detail={
                "error": {
                    "code": 429,
                    "message": f"Rate limited. Try again in {rl_result.retry_after}s",
                }
            },
            headers={"Retry-After": str(rl_result.retry_after)},
        )

    reactions = api.get_reactions()
    if not reactions:
        logger.error("Reactions module not available")
        raise HTTPException(
            status_code=503,
            detail={
                "error": {"code": 503, "message": "Reactions module not available"}
            },
        )

    try:
        try:
            mid = parse_id(message_id, "message_id")
        except (ValueError, TypeError):
            logger.warning(f"Invalid message ID format for reaction: {message_id}")
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid message ID"}},
            )

        try:
            cid = parse_id(channel_id, "channel_id")
        except (ValueError, TypeError):
            logger.warning(f"Invalid channel ID format for reaction: {channel_id}")
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid channel ID"}},
            )

        # Validate the message actually belongs to this channel BEFORE any
        # write or event emission (never trust the URL channel id).
        real_cid = _validate_message_in_channel(reactions, mid, cid)

        # Fail closed: writing a reaction requires the ability to VIEW the
        # channel. The core manager gates ``messages.add_reactions`` but treats
        # view access (``messages.read``) as a separate bit, so a server member
        # denied view by a channel override could otherwise react to a message
        # in a channel they cannot see (the reaction fan-out already hides such
        # members from events). Resolve the channel membership-agnostically and
        # check the live permission; DMs have no server channel row and are left
        # to the manager's own participant gate.
        servers_mod = api.get_servers()
        if servers_mod and hasattr(servers_mod, "get_channel_by_conversation"):
            conv_id = reactions.get_conversation_id_from_message(mid)
            srv_channel = (
                servers_mod.get_channel_by_conversation(conv_id) if conv_id else None
            )
            if srv_channel and not servers_mod.has_permission(
                current_user.user_id,
                srv_channel["server_id"],
                "messages.read",
                srv_channel["id"],
            ):
                raise HTTPException(
                    status_code=403,
                    detail={
                        "error": {
                            "code": 403,
                            "message": "Cannot react to this message",
                        }
                    },
                )

        # Check for server timeout
        servers_mod = api.get_servers()
        if servers_mod:
            try:
                channel = servers_mod.get_channel(real_cid, current_user.user_id)
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
                if (
                    isinstance(e, HTTPException)
                    and getattr(e, "status_code", None) == 403
                ):
                    raise

        try:
            decoded_emoji = _validated_emoji(emoji)

            reactions.add_reaction(current_user.user_id, mid, decoded_emoji)
            _invalidate_reaction_read_cache()

            # Fire-and-forget WebSocket event dispatch (real channel id)
            asyncio.create_task(
                _dispatch_reaction_event(
                    "add", current_user.user_id, mid, real_cid, decoded_emoji
                )
            )

            return SuccessResponse(success=True, message=None)
        except Exception as e:
            exc_name = type(e).__name__
            if "NotFound" in exc_name:
                raise HTTPException(
                    status_code=404,
                    detail={"error": {"code": 404, "message": "Message not found"}},
                )
            elif "Exists" in exc_name:
                return SuccessResponse(success=True, message=None)
            elif "Permission" in exc_name:
                logger.warning(
                    f"User {current_user.user_id} denied permission to react to message {mid}"
                )
                raise HTTPException(
                    status_code=403,
                    detail={"error": {"code": 403, "message": "Access denied"}},
                )
            elif "Invalid" in exc_name or "Limit" in exc_name:
                raise HTTPException(
                    status_code=400,
                    detail={"error": {"code": 400, "message": "Invalid request"}},
                )
            elif "Blocked" in exc_name:
                raise HTTPException(
                    status_code=403,
                    detail={
                        "error": {
                            "code": 403,
                            "message": "Cannot react to this message",
                        }
                    },
                )

            logger.error(
                f"Failed to add reaction to message {mid} for user {current_user.user_id}: {e}",
                exc_info=True,
            )
            raise HTTPException(
                status_code=500,
                detail={
                    "error": {
                        "code": 500,
                        "message": "Failed to add reaction",
                    }
                },
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Unexpected error in add_reaction for message {message_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.delete(
    "/channels/{channel_id}/messages/{message_id}/reactions/{emoji:path}/all",
    response_model=SuccessResponse,
    summary="Remove all reactions of one emoji (moderator)",
    responses={
        400: {
            "model": ErrorResponse,
            "description": "Invalid message ID or channel ID or emoji",
        },
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {
            "model": ErrorResponse,
            "description": "Not authorized to remove reactions",
        },
        404: {"model": ErrorResponse, "description": "Message not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
        503: {"model": ErrorResponse, "description": "Reactions module not available"},
    },
)
async def clear_reactions_for_emoji(
    channel_id: str,
    message_id: str,
    emoji: str,
    request: Request,
    current_user: TokenInfo = Depends(get_current_user),
) -> SuccessResponse:
    """
    Moderator action: remove every reaction of a specific emoji from a message.

    Requires manage-messages permission. Emits MESSAGE_REACTION_REMOVE_EMOJI
    to connected clients so reaction pills update live without a refresh.

    NOTE: this route MUST be declared BEFORE the single-user
    ``DELETE .../reactions/{emoji:path}`` route. Starlette matches routes in
    registration order and the ``path`` convertor is greedy, so a later
    declaration would swallow ``.../reactions/<emoji>/all`` as emoji
    "<emoji>/all" and the moderator wipe would be unreachable.
    """
    # A bot token's ``user_id`` is its human owner: reject it before the
    # owner-scoped rate-limit bucket is charged below.
    require_user_token(current_user)
    rl_result = await ratelimit.acheck_rate_limit(
        user_id=current_user.user_id,
        route="DELETE /reactions/emoji/all",
        is_internal=getattr(request.state, "is_internal", False),
    )
    if not rl_result.allowed:
        raise HTTPException(
            status_code=429,
            detail={
                "error": {
                    "code": 429,
                    "message": f"Rate limited. Try again in {rl_result.retry_after}s",
                }
            },
            headers={"Retry-After": str(rl_result.retry_after)},
        )

    reactions = api.get_reactions()
    if not reactions:
        raise HTTPException(
            status_code=503,
            detail={
                "error": {"code": 503, "message": "Reactions module not available"}
            },
        )

    try:
        mid = parse_id(message_id, "message_id")
        cid = parse_id(channel_id, "channel_id")
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Invalid message or channel ID"}},
        )

    # Validate the message actually belongs to this channel BEFORE any write
    # or event emission.
    real_cid = _validate_message_in_channel(reactions, mid, cid)

    # Fail closed: clearing reactions requires view access to the channel.
    _require_channel_view(reactions, current_user.user_id, mid, "remove reactions from")

    try:
        decoded_emoji = _validated_emoji(emoji)

        removed = reactions.remove_all_reactions_for_emoji(
            current_user.user_id, mid, decoded_emoji
        )
    except Exception as e:
        exc_name = type(e).__name__
        if "NotFound" in exc_name:
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": 404, "message": "Message not found"}},
            )
        if "Forbidden" in exc_name or "Permission" in exc_name:
            raise HTTPException(
                status_code=403,
                detail={
                    "error": {
                        "code": 403,
                        "message": "Not authorized to remove these reactions",
                    }
                },
            )
        if "Invalid" in exc_name or "Limit" in exc_name:
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid request"}},
            )
        logger.error(
            f"Failed to remove emoji reactions for message {mid}: {e}", exc_info=True
        )
        raise HTTPException(
            status_code=500,
            detail={
                "error": {"code": 500, "message": "Failed to remove emoji reactions"}
            },
        )

    _invalidate_reaction_read_cache()

    # Fire-and-forget WebSocket event dispatch so clients drop stale pills.
    asyncio.create_task(
        _dispatch_reaction_event(
            "remove_emoji", current_user.user_id, mid, real_cid, decoded_emoji
        )
    )

    return SuccessResponse(success=True, message=f"Removed {removed} reactions")


@router.delete(
    "/channels/{channel_id}/messages/{message_id}/reactions/{emoji:path}",
    response_model=SuccessResponse,
    summary="Remove reaction",
    responses={
        400: {
            "model": ErrorResponse,
            "description": "Invalid message ID or channel ID or emoji",
        },
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        404: {"model": ErrorResponse, "description": "Message not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
        503: {"model": ErrorResponse, "description": "Reactions module not available"},
    },
)
async def remove_reaction(
    channel_id: str,
    message_id: str,
    emoji: str,
    request: Request,
    current_user: TokenInfo = Depends(get_current_user),
) -> SuccessResponse:
    """
    Remove own reaction from a message.

    Removes the specified emoji reaction from the message.
    """
    # A bot token's ``user_id`` is its human owner: reject it before the
    # owner-scoped rate-limit bucket is charged below.
    require_user_token(current_user)
    # Rate limit reaction removal: 15 per second per user
    rl_result = await ratelimit.acheck_rate_limit(
        user_id=current_user.user_id,
        route="DELETE /reactions",
        is_internal=getattr(request.state, "is_internal", False),
    )
    if not rl_result.allowed:
        raise HTTPException(
            status_code=429,
            detail={
                "error": {
                    "code": 429,
                    "message": f"Rate limited. Try again in {rl_result.retry_after}s",
                }
            },
            headers={"Retry-After": str(rl_result.retry_after)},
        )

    reactions = api.get_reactions()
    if not reactions:
        logger.error("Reactions module not available")
        raise HTTPException(
            status_code=503,
            detail={
                "error": {"code": 503, "message": "Reactions module not available"}
            },
        )

    try:
        try:
            mid = parse_id(message_id, "message_id")
        except (ValueError, TypeError):
            logger.warning(
                f"Invalid message ID format for reaction removal: {message_id}"
            )
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid message ID"}},
            )

        try:
            cid = parse_id(channel_id, "channel_id")
        except (ValueError, TypeError):
            logger.warning(
                f"Invalid channel ID format for reaction removal: {channel_id}"
            )
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid channel ID"}},
            )

        # Fail closed: removing a reaction requires view access to the channel.
        # Kept outside the inner try so its 403 is not rewritten to a 500.
        _require_channel_view(
            reactions, current_user.user_id, mid, "remove reactions from"
        )

        try:
            # Validate the message actually belongs to this channel BEFORE any
            # write or event emission.
            real_cid = _validate_message_in_channel(reactions, mid, cid)

            # URL decode the emoji if needed

            decoded_emoji = _validated_emoji(emoji)

            reactions.remove_reaction(current_user.user_id, mid, decoded_emoji)
            _invalidate_reaction_read_cache()

            # Fire-and-forget WebSocket event dispatch (real channel id)
            asyncio.create_task(
                _dispatch_reaction_event(
                    "remove", current_user.user_id, mid, real_cid, decoded_emoji
                )
            )

            return SuccessResponse(success=True, message=None)
        except InvalidEmojiError:
            # M5: invalid emoji on remove is a client error (400), not a
            # server error. Explicit class handler (mirrors the PUT
            # add_reaction Invalid -> 400 mapping); genuine server errors
            # still fall through to the generic 500 below.
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid emoji"}},
            )
        except Exception as e:
            exc_name = type(e).__name__
            if "NotFound" in exc_name:
                return SuccessResponse(success=True, message=None)
            elif "Permission" in exc_name:
                logger.warning(
                    f"User {current_user.user_id} denied permission to remove reaction on message {mid}"
                )
                raise HTTPException(
                    status_code=403,
                    detail={"error": {"code": 403, "message": "Access denied"}},
                )
            elif "Blocked" in exc_name:
                raise HTTPException(
                    status_code=403,
                    detail={
                        "error": {
                            "code": 403,
                            "message": "Cannot react to this message",
                        }
                    },
                )
            elif "Invalid" in exc_name:
                # M5: removing a malformed/legacy-now-invalid emoji is a CLIENT
                # error, mirroring the PUT handler's Invalid mapping instead of
                # falling through to a 500 with exc_info logging.
                raise HTTPException(
                    status_code=400,
                    detail={"error": {"code": 400, "message": "Invalid emoji"}},
                )

            logger.error(
                f"Failed to remove reaction from message {mid} for user {current_user.user_id}: {e}",
                exc_info=True,
            )
            raise HTTPException(
                status_code=500,
                detail={"error": {"code": 500, "message": "Failed to remove reaction"}},
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Unexpected error in remove_reaction for message {message_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.delete(
    "/channels/{channel_id}/messages/{message_id}/reactions",
    response_model=SuccessResponse,
    summary="Clear all reactions",
    responses={
        400: {
            "model": ErrorResponse,
            "description": "Invalid message ID or channel ID",
        },
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {
            "model": ErrorResponse,
            "description": "Not authorized to clear reactions",
        },
        500: {"model": ErrorResponse, "description": "Internal server error"},
        503: {"model": ErrorResponse, "description": "Reactions module not available"},
    },
)
async def clear_reactions(
    channel_id: str,
    message_id: str,
    request: Request,
    current_user: TokenInfo = Depends(get_current_user),
) -> SuccessResponse:
    """
    Remove all reactions from a message.

    Emits MESSAGE_REACTION_REMOVE_ALL to connected clients so reaction
    pills update live without a refresh.
    """
    # A bot token's ``user_id`` is its human owner: reject it before the
    # owner-scoped rate-limit bucket is charged below.
    require_user_token(current_user)
    rl_result = await ratelimit.acheck_rate_limit(
        user_id=current_user.user_id,
        route="DELETE /reactions/all",
        is_internal=getattr(request.state, "is_internal", False),
    )
    if not rl_result.allowed:
        raise HTTPException(
            status_code=429,
            detail={
                "error": {
                    "code": 429,
                    "message": f"Rate limited. Try again in {rl_result.retry_after}s",
                }
            },
            headers={"Retry-After": str(rl_result.retry_after)},
        )

    reactions = api.get_reactions()
    if not reactions:
        raise HTTPException(
            status_code=503,
            detail={
                "error": {"code": 503, "message": "Reactions module not available"}
            },
        )

    try:
        mid = parse_id(message_id, "message_id")
        cid = parse_id(channel_id, "channel_id")
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Invalid message or channel ID"}},
        )

    # Validate the message actually belongs to this channel BEFORE any write
    # or event emission.
    real_cid = _validate_message_in_channel(reactions, mid, cid)

    # Fail closed: clearing all reactions requires view access to the channel.
    _require_channel_view(reactions, current_user.user_id, mid, "clear reactions on")

    try:
        reactions.remove_all_reactions(current_user.user_id, mid)
    except Exception as e:
        exc_name = type(e).__name__
        if "NotFound" in exc_name:
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": 404, "message": "Message not found"}},
            )
        if "Forbidden" in exc_name or "Permission" in exc_name:
            raise HTTPException(
                status_code=403,
                detail={
                    "error": {
                        "code": 403,
                        "message": "Not authorized to clear reactions",
                    }
                },
            )
        logger.error(f"Failed to clear reactions for message {mid}: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Failed to clear reactions"}},
        )

    _invalidate_reaction_read_cache()

    from src.core.events.gateway_emit import emit_message_reaction_remove_all

    emit_message_reaction_remove_all(
        channel_id=real_cid,
        message_id=mid,
        actor_id=current_user.user_id,
    )
    return SuccessResponse(success=True, message=None)


@router.get(
    "/channels/{channel_id}/messages/{message_id}/reactions/{emoji:path}",
    response_model=List[ReactionUserResponse],
    summary="Get reaction users",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid message ID"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        404: {"model": ErrorResponse, "description": "Message not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_reaction_users(
    channel_id: str,
    message_id: str,
    emoji: str,
    limit: int = Query(default=50, ge=1, le=100),
    after: Optional[str] = Query(default=None, max_length=64),
    current_user: TokenInfo = Depends(get_current_user),
) -> List[ReactionUserResponse]:
    """
    Get users who reacted with an emoji.

    Returns a list of users who added the specified reaction.
    """
    reactions = api.get_reactions()
    if not reactions:
        logger.error("Reactions module not available")
        raise HTTPException(
            status_code=500,
            detail={
                "error": {"code": 500, "message": "Reactions module not available"}
            },
        )

    try:
        try:
            mid = parse_id(message_id, "message_id")
        except (ValueError, TypeError):
            logger.warning(
                f"Invalid message ID format for reaction users: {message_id}"
            )
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid message ID"}},
            )

        # The channel id is part of this route's authorization scope, so it
        # must be parsed by the same ASCII snowflake gate as every other
        # reaction route. It used to be accepted verbatim and then ignored.
        try:
            cid = parse_id(channel_id, "channel_id")
        except (ValueError, TypeError):
            logger.warning(
                f"Invalid channel ID format for reaction users: {channel_id}"
            )
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid channel ID"}},
            )

        try:
            # ``after`` is a user-id pagination cursor, so it must pass the
            # same ASCII-only snowflake gate as ``message_id`` above. Bare
            # ``int()`` accepted Unicode digit homoglyphs ("٤٢", "４２"), PEP
            # 515 underscores ("1_2" -> 12), negatives and values past
            # 2**63-1. Those then reached the ``user_id > ?`` cursor predicate
            # with a different meaning than the raw token, and a value above
            # the SQLite INTEGER range raised an OverflowError at bind time
            # (surfacing as a 500), so the same bytes were interpreted
            # differently by the boundary and the manager. Route the cursor
            # through the shared bounded parser so validation and consumption
            # agree. Keep the lower bound at 0 to preserve the historical
            # "0 means no cursor" behaviour.
            after_id = (
                bounded_int(after, "after", minimum=0, maximum=SNOWFLAKE_MAX)
                if after
                else None
            )
        except (ValueError, TypeError):
            logger.warning(f"Invalid 'after' ID format for reaction users: {after}")
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid after ID"}},
            )

        try:
            # URL decode the emoji to match how it is stored (add/remove
            # decode on the server side; the client sends it encoded).

            decoded_emoji = _validated_emoji(emoji)

            # Live object-level authorization before any cached roster read.
            # The production manager always exposes this gate; the guard keeps
            # lightweight test stubs (whose get_reaction_users still performs
            # its own participant check) working with an interface they never
            # implemented.
            gate = getattr(reactions, "can_access_message", None)
            if gate is not None and not gate(current_user.user_id, mid):
                raise MessageNotFoundError("Message not found")

            # Never trust the URL channel id: verify the message really lives
            # in it, exactly as the add/remove/clear routes do.
            if hasattr(reactions, "resolve_message_channel"):
                _validate_message_in_channel(reactions, mid, cid)

            users = reactions.get_reaction_users(
                user_id=current_user.user_id,
                message_id=mid,
                emoji=decoded_emoji,
                limit=limit,
                after_user_id=after_id,
            )

            return [
                ReactionUserResponse(
                    user_id=SnowflakeID(u.user_id), reacted_at=u.reacted_at
                )
                for u in users
            ]
        except HTTPException:
            # _validate_message_in_channel already produced the right status.
            raise
        except Exception as e:
            exc_name = type(e).__name__
            if "NotFound" in exc_name:
                raise HTTPException(
                    status_code=404,
                    detail={"error": {"code": 404, "message": "Message not found"}},
                )

            logger.error(
                f"Failed to fetch reaction users for message {mid}: {e}", exc_info=True
            )
            raise HTTPException(
                status_code=500,
                detail={
                    "error": {
                        "code": 500,
                        "message": "Failed to fetch reaction users",
                    }
                },
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Unexpected error in get_reaction_users for message {message_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.get(
    "/channels/{channel_id}/messages/{message_id}/reactions",
    response_model=List[ReactionResponse],
    summary="Get reactions",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid message ID"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        404: {"model": ErrorResponse, "description": "Message not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_reactions(
    channel_id: str,
    message_id: str,
    current_user: TokenInfo = Depends(get_current_user),
) -> List[ReactionResponse]:
    """
    Get all reactions on a message.

    Returns a list of all emoji reactions with counts.
    """
    reactions = api.get_reactions()
    if not reactions:
        logger.error("Reactions module not available")
        raise HTTPException(
            status_code=500,
            detail={
                "error": {"code": 500, "message": "Reactions module not available"}
            },
        )

    try:
        try:
            mid = parse_id(message_id, "message_id")
        except (ValueError, TypeError):
            logger.warning(f"Invalid message ID format for reactions: {message_id}")
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid message ID"}},
            )

        try:
            # Live authz gate on EVERY request, before the cache lookup: a
            # viewer who lost channel access must not receive a warm body.
            if not reactions.can_access_message(current_user.user_id, mid):
                raise MessageNotFoundError("Message not found")

            data = await _load_reactions(message_id=mid, current_user=current_user)
            return [ReactionResponse.model_validate(item) for item in data]
        except Exception as e:
            exc_name = type(e).__name__
            if "NotFound" in exc_name:
                raise HTTPException(
                    status_code=404,
                    detail={"error": {"code": 404, "message": "Message not found"}},
                )

            logger.error(
                f"Failed to fetch reactions for message {mid}: {e}", exc_info=True
            )
            raise HTTPException(
                status_code=500,
                detail={
                    "error": {
                        "code": 500,
                        "message": "Failed to fetch reactions",
                    }
                },
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Unexpected error in get_reactions for message {message_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )
