"""
Presence routes - User status and presence endpoints.
"""

from typing import Optional

from fastapi import APIRouter, HTTPException, Depends

import src.api as api
from src.core.database import cached
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.presence import PresenceUpdate, PresenceResponse
from src.api.schemas.common import SnowflakeID, ErrorResponse
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
                """All synchronous DB/logic calls in one threadpool execution."""
                import src.api as api

                db = api.get_db()
                try:
                    status_enum = UserStatus(body.status)
                    presence.set_status(current_user.user_id, status_enum)

                    if body.custom_status or body.custom_emoji:
                        presence.set_custom_status(
                            user_id=current_user.user_id,
                            text=(body.custom_status or ""),
                            emoji=body.custom_emoji,
                        )
                    else:
                        presence.clear_custom_status(current_user.user_id)

                    return presence.get_presence(current_user.user_id)
                finally:
                    if db:
                        db.close()

            pres = await run_in_threadpool(perform_presence_update)
            response = _presence_to_response(pres, current_user.user_id)

            # Get all users who should receive this presence update (uses cached function)
            target_user_ids = _get_presence_targets(current_user.user_id)

            # CORRECTNESS FIX: never leak custom status for an invisible user.
            # The event data must be scrubbed the same way the REST response
            # and ``get_visible_presence`` are.
            presence_data = _build_presence_event_data(
                current_user.user_id,
                body.status,
                body.custom_status,
                body.custom_emoji,
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
                    status_code=400, detail={"error": {"code": 400, "message": str(e)}}
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
                        "message": f"Failed to update presence: {str(e)}",
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
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )


@router.get(
    "/users/{user_id}/presence",
    response_model=PresenceResponse,
    summary="Get user presence",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid user ID"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
def get_user_presence(
    user_id: str, current_user: TokenInfo = Depends(get_current_user)
) -> PresenceResponse:
    """
    Get a user's presence.

    Returns the user's current online status and custom status.
    """
    presence = api.get_presence()
    if not presence:
        logger.error("Presence module not available")
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Presence module not available"}},
        )

    try:
        try:
            uid = int(user_id)
        except (ValueError, TypeError):
            logger.warning(f"Invalid user ID format for presence request: {user_id}")
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid user ID"}},
            )

        try:
            pres = presence.get_visible_presence(current_user.user_id, uid)
            if not pres:
                return PresenceResponse(
                    user_id=SnowflakeID(uid),
                    status="offline",
                    custom_status=None,
                    custom_emoji=None,
                    last_seen=None,
                )
            return _presence_to_response(pres, uid)
        except Exception as e:
            exc_name = type(e).__name__
            if "NotFound" in exc_name:
                return PresenceResponse(
                    user_id=SnowflakeID(uid),
                    status="offline",
                    custom_status=None,
                    custom_emoji=None,
                    last_seen=None,
                )

            logger.error(
                f"Failed to fetch presence for user {uid} (requested by {current_user.user_id}): {e}",
                exc_info=True,
            )
            raise HTTPException(
                status_code=500,
                detail={
                    "error": {
                        "code": 500,
                        "message": f"Failed to fetch presence: {str(e)}",
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
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )
