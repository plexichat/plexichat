"""
Presence routes - User status and presence endpoints.
"""

from typing import Optional
from fastapi import APIRouter, HTTPException, Depends

import src.api as api
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.presence import PresenceUpdate, PresenceResponse

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
        user_id=str(user_id),
        status=status or "offline",
        custom_status=custom_text,
        custom_emoji=custom_emoji,
        last_seen=getattr(pres, "last_seen", None),
    )


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
        presence.set_status(current_user.user_id, body.status)
        
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
        return _presence_to_response(pres, current_user.user_id)
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
                user_id=str(uid),
                status="offline",
            )
        return _presence_to_response(pres, uid)
    except Exception as e:
        exc_name = type(e).__name__
        if "NotFound" in exc_name:
            return PresenceResponse(
                user_id=str(uid),
                status="offline",
            )
        raise
