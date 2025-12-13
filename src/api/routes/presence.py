"""
Presence routes - User status and presence endpoints.
"""

from fastapi import APIRouter, HTTPException, Depends

import src.api as api
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.presence import PresenceUpdate, PresenceResponse
from src.api.schemas.common import SnowflakeID

router = APIRouter()


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

    return PresenceResponse(
        user_id=SnowflakeID(user_id),
        status=status or "offline",
        custom_status=custom_text,
        custom_emoji=custom_emoji,
        last_seen=getattr(pres, "last_seen", None),
    )


async def _dispatch_presence_event(user_id: int, presence_data: dict, target_user_ids: list):
    """Helper to dispatch presence update events via WebSocket."""
    try:
        from src.api.websocket import get_dispatcher, is_setup as ws_is_setup
        from src.core.events.models import Event
        from src.core.events.types import EventType

        if ws_is_setup() and target_user_ids:
            dispatcher = get_dispatcher()
            event = Event(
                event_type=EventType.PRESENCE_UPDATE,
                data=presence_data
            )
            # Send to all users who should see this presence update
            await dispatcher.dispatch_event(event, target_user_ids)
    except Exception as e:
        import utils.logger as logger
        logger.debug(f"Failed to dispatch presence event: {e}")


async def _get_presence_targets(user_id: int) -> list:
    """Get all user IDs who should receive presence updates for a user."""
    target_user_ids = set()

    # Add friends
    relationships = api.get_relationships()
    if relationships:
        try:
            friend_ids = relationships.get_friend_ids(user_id)
            if friend_ids:
                target_user_ids.update(friend_ids)
        except Exception:
            pass

    # Add server members (users in shared servers)
    servers = api.get_servers()
    if servers:
        try:
            user_servers = servers.get_servers(user_id)
            if user_servers:
                for server in user_servers:
                    members = servers.get_members(user_id, server.id)
                    if members:
                        for member in members:
                            if member.user_id != user_id:
                                target_user_ids.add(member.user_id)
        except Exception:
            pass

    return list(target_user_ids)


@router.put("/users/@me/presence", response_model=PresenceResponse)
async def update_presence(
    body: PresenceUpdate,
    current_user: TokenInfo = Depends(get_current_user)
):
    """
    Update current user's presence.
    
    Sets the user's online status and custom status message.
    """
    presence = api.get_presence()
    if not presence:
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Presence module not available"}})

    valid_statuses = ["online", "idle", "dnd", "invisible", "offline"]
    if body.status not in valid_statuses:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": f"Invalid status. Must be one of: {', '.join(valid_statuses)}"}}
        )

    try:
        from src.core.presence.models import UserStatus
        status_enum = UserStatus(body.status)
        presence.set_status(current_user.user_id, status_enum)

        if body.custom_status is not None or body.custom_emoji is not None:
            if body.custom_status or body.custom_emoji:
                presence.set_custom_status(
                    user_id=current_user.user_id,
                    text=body.custom_status,
                    emoji=body.custom_emoji
                )
            else:
                presence.clear_custom_status(current_user.user_id)

        pres = presence.get_presence(current_user.user_id)
        response = _presence_to_response(pres, current_user.user_id)

        # Get all users who should receive this presence update
        target_user_ids = await _get_presence_targets(current_user.user_id)

        # For invisible status, show as offline to others
        visible_status = body.status if body.status != "invisible" else "offline"

        if target_user_ids:
            await _dispatch_presence_event(current_user.user_id, {
                "user_id": str(current_user.user_id),
                "status": visible_status,
                "custom_status": body.custom_status,
                "custom_emoji": body.custom_emoji,
            }, target_user_ids)

        return response
    except Exception as e:
        exc_name = type(e).__name__
        if "Invalid" in exc_name:
            raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": str(e)}})
        raise


@router.get("/users/{user_id}/presence", response_model=PresenceResponse)
async def get_user_presence(user_id: str, current_user: TokenInfo = Depends(get_current_user)):
    """
    Get a user's presence.
    
    Returns the user's current online status and custom status.
    """
    presence = api.get_presence()
    if not presence:
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Presence module not available"}})

    try:
        uid = int(user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": "Invalid user ID"}})

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
        raise
