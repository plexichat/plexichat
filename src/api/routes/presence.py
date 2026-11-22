"""
Presence routes - User status and presence endpoints.
"""

from typing import Optional

from fastapi import APIRouter, HTTPException, Depends

import src.api as api
from src.core.database import cached, invalidate_pattern
from src.api.routes.users.helpers import authenticated_cache_key
from src.core.presence.validation import (
    validate_emoji,
    validate_status,
    validate_status_text,
)
from src.core.security.hardening import HardeningViolation
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.presence import PresenceUpdate, PresenceResponse
from src.api.schemas.common import ErrorResponse, SnowflakeID, SnowflakeIDParam
import utils.logger as logger

router = APIRouter(tags=["Presence"])


def _presence_to_response(pres, user_id: int) -> PresenceResponse:
    """Convert presence object to response model."""
    status = getattr(pres, "status", None)
    if status is not None and hasattr(status, "value"):
        status = status.value

    custom = getattr(pres, "custom_status", None)
    custom_text = None
    custom_emoji = None
    if custom:
        custom_text = getattr(custom, "text", None)
        custom_emoji = getattr(custom, "emoji", None)

    # CORRECTNESS FIX: a never-online user has last_seen == 0 in the DB.
    # Exposing 0 leaks the fact that the user exists but has never been seen;
    # report None instead so offline users are indistinguishable.
    last_seen = getattr(pres, "last_seen", None)
    if not last_seen:
        last_seen = None

    return PresenceResponse(
        user_id=SnowflakeID(user_id),
        status=status or "offline",
        custom_status=custom_text,
        custom_emoji=custom_emoji,
        last_seen=last_seen,
    )


def _build_presence_event_data(
    user_id: int,
    status: str,
    custom_status: Optional[str],
    custom_emoji: Optional[str],
) -> dict:
    """Build the PRESENCE_UPDATE event payload, scrubbing invisible state.

    An invisible user must always appear offline with no custom status to
    everyone else, exactly like ``get_visible_presence`` renders them.
    """
    is_invisible = status == "invisible"
    return {
        "user_id": str(user_id),
        "status": "offline" if is_invisible else status,
        "custom_status": None if is_invisible else custom_status,
        "custom_emoji": None if is_invisible else custom_emoji,
    }


async def _dispatch_presence_event(
    user_id: int, presence_data: dict, target_user_ids: list
):
    """
    Broadcast a presence update event to specific users via WebSocket.
    """
    try:
        from src.api.websocket import get_dispatcher, is_setup as ws_is_setup
        from src.core.events.models import Event
        from src.core.events.types import EventType

        if ws_is_setup() and target_user_ids:
            dispatcher = get_dispatcher()
            event = Event(event_type=EventType.PRESENCE_UPDATE, data=presence_data)
            # Send to all users who should see this presence update
            await dispatcher.dispatch_event(event, target_user_ids)
    except Exception as e:
        logger.debug(f"Failed to dispatch presence event for user {user_id}: {e}")


@cached(ttl=5, prefix="presence_targets")
def _get_presence_targets(user_id: int) -> list:
    """
    Identify all users who should receive presence notifications for a given user.

    Includes friends and members of shared servers. Uses caching to minimize database load.
    """
    target_user_ids = set()

    # 1. Add friends
    relationships = api.get_relationships()
    if relationships:
        try:
            friend_ids = relationships.get_friend_ids(user_id)
            if friend_ids:
                # friend_ids should be a flat list of ints/SnowflakeIDs
                for fid in friend_ids:
                    try:
                        target_user_ids.add(int(fid))
                    except (ValueError, TypeError):
                        continue
        except Exception as e:
            logger.debug(f"Error fetching friend IDs: {e}")

    # 2. Add ALL server members in one query (shared servers)
    servers = api.get_servers()
    if servers:
        try:
            if hasattr(servers, "get_all_shared_member_ids"):
                shared_ids = servers.get_all_shared_member_ids(user_id)
                if shared_ids:
                    for sid in shared_ids:
                        try:
                            target_user_ids.add(int(sid))
                        except (ValueError, TypeError):
                            continue
            else:
                # Fallback to slower server-by-server fetch if method missing
                user_servers = servers.get_servers(user_id)
                if user_servers:
                    for server in user_servers:
                        member_ids = servers.get_member_user_ids(
                            server.id, exclude_user_id=user_id
                        )
                        if member_ids:
                            for mid in member_ids:
                                try:
                                    target_user_ids.add(int(mid))
                                except (ValueError, TypeError):
                                    continue
        except Exception as e:
            logger.debug(f"Error fetching shared members: {e}")

    # CORRECTNESS FIX: presence updates must not reach users who block (or are
    # blocked by) the target — blocked users should always observe the target
    # as offline, so they must never receive real presence events either.
    if relationships:
        try:
            blocked_ids = relationships.get_all_blocked_ids(user_id)
            for bid in blocked_ids:
                try:
                    target_user_ids.discard(int(bid))
                except (ValueError, TypeError):
                    continue
        except Exception as e:
            logger.debug(f"Error filtering blocked users from presence targets: {e}")
            # FAIL CLOSED: get_all_blocked_ids is the authoritative two-way
            # block set. Without it no candidate recipient is proven safe, so
            # withhold the whole audience rather than broadcast the subject's
            # real presence to a user who blocked them (or whom they blocked).
            return []

    # Remove self just in case
    target_user_ids.discard(user_id)
    return list(target_user_ids)


@router.put(
    "/users/@me/presence",
    response_model=PresenceResponse,
    summary="Update my presence",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid status or data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def update_presence(
    body: PresenceUpdate, current_user: TokenInfo = Depends(get_current_user)
) -> PresenceResponse:
    """
    Update current user's presence.

    Sets the user's online status and custom status message.
    """
    # Every route path validates status, text and emoji through the shared
    # helpers, not just the schema layer, so a direct or internal caller cannot
    # smuggle a control character, an unbounded token or a multi-KB status into
    # the event stream.
    try:
        validate_status(body.status)
        validate_status_text(body.custom_status)
        validate_emoji(body.custom_emoji)
    except HardeningViolation as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Invalid status, text or emoji"}},
        ) from exc

    presence = api.get_presence()
    if not presence:
        logger.error("Presence module not available")
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Presence module not available"}},
        )

    valid_statuses = ["online", "idle", "dnd", "invisible", "offline"]
    if body.status not in valid_statuses:
        logger.warning(
            f"User {current_user.user_id} provided invalid presence status: {body.status}"
        )
        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "code": 400,
                    "message": f"Invalid status. Must be one of: {', '.join(valid_statuses)}",
                }
            },
        )

    try:
        try:
            from src.core.presence.models import UserStatus
            from fastapi.concurrency import run_in_threadpool

            def perform_presence_update():
                """All synchronous DB/logic calls in one threadpool execution.

                NOTE: the presence manager owns its own database connection; we
                must NOT open or close ``api.get_db()`` here. ``api.get_db()``
                returns the process-wide shared connection, so closing it would
                break every subsequent request in the whole server.
                """
                status_enum = UserStatus(body.status)
                presence.set_status(current_user.user_id, status_enum)

                # Only touch the custom status when the client explicitly sends
                # it. A status-only update (the common case from the UI) must NOT
                # wipe an existing custom status/emoji — see P1. The WebSocket
                # handler already preserves on omission; this REST path must
                # match, otherwise the HTTP call clears then the WS call can
                # only re-read the already-cleared value.
                if body.custom_status is not None or body.custom_emoji is not None:
                    text = (body.custom_status or "").strip()
                    emoji = (
                        (body.custom_emoji or "").strip() if body.custom_emoji else None
                    )
                    # CORRECTNESS FIX: an emoji-only status (empty text,
                    # non-empty emoji) is a valid persisted status — the core
                    # manager explicitly supports it. Only empty-text AND
                    # empty-emoji clears the custom status.
                    if text or emoji:
                        presence.set_custom_status(
                            user_id=current_user.user_id,
                            text=text,
                            emoji=emoji,
                        )
                    else:
                        presence.clear_custom_status(current_user.user_id)

                return presence.get_presence(current_user.user_id)

            pres = await run_in_threadpool(perform_presence_update)
            # A status/custom-status change invalidates any warm single-user
            # presence bodies for this user, including invisible -> visible
            # transitions that other viewers would otherwise keep seeing stale.
            _invalidate_presence_read_cache()
            response = _presence_to_response(pres, current_user.user_id)

            # Get all users who should receive this presence update (uses cached function)
            target_user_ids = _get_presence_targets(current_user.user_id)

            # CORRECTNESS FIX: never leak custom status for an invisible user.
            # The event data must be scrubbed the same way the REST response
            # and ``get_visible_presence`` are. Broadcast the *actual* persisted
            # custom status so a status-only update does not hide an existing
            # one (P1).
            actual_custom = pres.custom_status.text if pres.custom_status else None
            actual_emoji = pres.custom_status.emoji if pres.custom_status else None
            presence_data = _build_presence_event_data(
                current_user.user_id,
                body.status,
                actual_custom,
                actual_emoji,
            )

            if target_user_ids:
                import asyncio

                asyncio.create_task(
                    _dispatch_presence_event(
                        current_user.user_id, presence_data, target_user_ids
                    )
                )

            return response
        except Exception as e:
            exc_name = type(e).__name__
            if "Invalid" in exc_name:
                raise HTTPException(
                    status_code=400,
                    detail={"error": {"code": 400, "message": "Invalid request"}},
                )
            if "NotFound" in exc_name:
                # A target user id may have been soft-deleted; surface a clean
                # 404 instead of a 500 (P2).
                raise HTTPException(
                    status_code=404,
                    detail={"error": {"code": 404, "message": "Resource not found"}},
                )

            logger.error(
                f"Failed to update presence for user {current_user.user_id}: {e}",
                exc_info=True,
            )
            raise HTTPException(
                status_code=500,
                detail={
                    "error": {
                        "code": 500,
                        "message": "Failed to update presence",
                    }
                },
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Unexpected error in update_presence for user {current_user.user_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


def _can_view_presence(viewer_id: int, target_id: int) -> bool:
    """Relationship/membership gate for reading another user's presence.

    A viewer may read a target's presence when they are the target, share a
    friendship, or share at least one server. Everything else (strangers,
    blocked relationships) is denied; invisible/block scrubbing still applies
    on top of an allowed read.
    """
    if viewer_id == target_id:
        return True

    relationships = api.get_relationships()
    if relationships:
        try:
            friend_ids = relationships.get_friend_ids(viewer_id)
            if friend_ids and int(target_id) in {int(f) for f in friend_ids}:
                return True
        except Exception as e:
            logger.debug(f"Friend check failed for presence gate: {e}")

    servers = api.get_servers()
    if servers:
        try:
            if hasattr(servers, "get_all_shared_member_ids"):
                shared_ids = servers.get_all_shared_member_ids(viewer_id)
                if shared_ids and int(target_id) in {int(s) for s in shared_ids}:
                    return True
        except Exception as e:
            logger.debug(f"Shared-server check failed for presence gate: {e}")

    return False


# GET /users/{id}/presence is a hot poll: on every cold read the core visibility
# layer issues two uncached block lookups plus a presence fetch before the
# response is serialized. Memoize the serialized body for a short window. The
# key is partitioned per credential by ``authenticated_cache_key`` (a bot token
# carries its owner's ``user_id``, so it needs its own namespace), and the route
# re-runs the live relationship gate on *every* request before this helper is
# consulted -- a denied viewer never reaches a warm entry, and a revoked
# friendship cannot be served a still-warm body. The L1 copy is deliberately
# shorter than the shared one to bound cross-worker staleness after invalidation.
_PRESENCE_READ_CACHE_TTL = 5
_PRESENCE_READ_L1_TTL = 2
_PRESENCE_READ_PREFIX = "presence_read_api"


def _invalidate_presence_read_cache() -> None:
    """Drop all cached single-user presence bodies after a mutation."""
    invalidate_pattern(f"{_PRESENCE_READ_PREFIX}:*")


@cached(
    ttl=_PRESENCE_READ_CACHE_TTL,
    prefix=_PRESENCE_READ_PREFIX,
    key_builder=authenticated_cache_key(_PRESENCE_READ_PREFIX),
    l1_ttl=_PRESENCE_READ_L1_TTL,
)
def _get_visible_presence_cached(*, user_id: int, current_user: TokenInfo) -> dict:
    """Cold path for GET /users/{id}/presence.

    Returns a JSON-safe dict so a cache hit (which reconstructs a dict) and a
    miss are byte-for-byte identical. Authorization is deliberately NOT checked
    here: the caller evaluates the live relationship gate first, so this helper
    is only ever reached by a viewer who is allowed to see the target.
    """
    presence = api.get_presence()
    pres = (
        presence.get_visible_presence(current_user.user_id, user_id)
        if presence
        else None
    )
    if not pres:
        response = PresenceResponse(
            user_id=SnowflakeID(user_id),
            status="offline",
            custom_status=None,
            custom_emoji=None,
            last_seen=None,
        )
    else:
        response = _presence_to_response(pres, user_id)
    return response.model_dump(mode="json")


@router.get(
    "/users/{user_id}/presence",
    response_model=PresenceResponse,
    summary="Get user presence",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid user ID"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        404: {"model": ErrorResponse, "description": "No relationship with user"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
def get_user_presence(
    user_id: SnowflakeIDParam, current_user: TokenInfo = Depends(get_current_user)
) -> PresenceResponse:
    """
    Get a user's presence.

    Returns the user's current online status and custom status. Only the
    user themself, friends, and members of shared servers may read a
    presence; other requests are rejected with 404.
    """
    presence = api.get_presence()
    if not presence:
        logger.error("Presence module not available")
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Presence module not available"}},
        )

    # CORRECTNESS FIX: previously any authenticated user could poll the real
    # status/custom status/last_seen of arbitrary users by enumerating IDs.
    # Gate reads on self/friend/shared-server like the rest of the codebase.
    if not _can_view_presence(current_user.user_id, user_id):
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": 404, "message": "User not found"}},
        )

    try:
        try:
            # The relationship gate above is re-evaluated on every request,
            # before any cache lookup. The helper key is scoped to this viewer
            # and target, so no other credential's body can be served here.
            data = _get_visible_presence_cached(
                user_id=user_id, current_user=current_user
            )
            return PresenceResponse.model_validate(data)
        except Exception as e:
            exc_name = type(e).__name__
            if "NotFound" in exc_name:
                return PresenceResponse(
                    user_id=SnowflakeID(user_id),
                    status="offline",
                    custom_status=None,
                    custom_emoji=None,
                    last_seen=None,
                )

            logger.error(
                f"Failed to fetch presence for user {user_id} (requested by {current_user.user_id}): {e}",
                exc_info=True,
            )
            raise HTTPException(
                status_code=500,
                detail={
                    "error": {
                        "code": 500,
                        "message": "Failed to fetch presence",
                    }
                },
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Unexpected error in get_user_presence for user {user_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )
