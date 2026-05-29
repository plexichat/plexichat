from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from typing import Optional

import utils.logger as logger
import src.api as api
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.common import ErrorResponse
from .common import raise_bad_request, raise_forbidden, raise_internal

router = APIRouter()


class LastChatRequest(BaseModel):
    conversation_id: str = Field(..., description="Last active conversation ID")
    last_message_id: Optional[str] = Field(None, description="Last visible message ID")
    scroll_position: Optional[int] = Field(
        None, description="Scroll position for restoration"
    )


@router.put(
    "/users/@me/last-chat",
    summary="Save last active chat",
    responses={400: {"model": ErrorResponse}, 401: {"model": ErrorResponse}},
)
async def save_last_chat(
    body: LastChatRequest, current_user: TokenInfo = Depends(get_current_user)
):
    try:
        conversation_id = int(body.conversation_id)
        last_message_id = int(body.last_message_id) if body.last_message_id else None
    except ValueError:
        raise_bad_request("Invalid ID format")

    db = api.get_db()
    messaging = api.get_messaging()
    participant_svc = (
        getattr(messaging, "_participant_svc", None) if messaging else None
    )

    from src.core.messaging.services.last_chat import LastChatService

    svc = LastChatService(db, participant_svc)

    try:
        result = svc.save_last_chat(
            user_id=current_user.user_id,
            conversation_id=conversation_id,
            last_message_id=last_message_id,
            scroll_position=body.scroll_position
            if body.scroll_position is not None
            else 0,
        )
        return {"success": True, "last_chat": result}
    except PermissionError as e:
        raise_forbidden(str(e))
    except Exception as e:
        logger.error(f"Failed to save last chat for user {current_user.user_id}: {e}")
        raise_internal("Failed to save last chat state")


@router.get(
    "/users/@me/last-chat",
    summary="Get last active chat",
    responses={401: {"model": ErrorResponse}},
)
async def get_last_chat(
    current_user: TokenInfo = Depends(get_current_user),
):
    db = api.get_db()
    messaging = api.get_messaging()
    participant_svc = (
        getattr(messaging, "_participant_svc", None) if messaging else None
    )

    from src.core.messaging.services.last_chat import LastChatService

    svc = LastChatService(db, participant_svc)

    result = svc.get_last_chat(current_user.user_id)
    if not result:
        return {"last_chat": None}
    return {"last_chat": result}


@router.get(
    "/users/@me/recent-chats",
    summary="Get recent chat history",
    responses={401: {"model": ErrorResponse}},
)
async def get_recent_chats(
    limit: int = Query(10, ge=1, le=50),
    current_user: TokenInfo = Depends(get_current_user),
):
    db = api.get_db()
    messaging = api.get_messaging()
    participant_svc = (
        getattr(messaging, "_participant_svc", None) if messaging else None
    )

    from src.core.messaging.services.last_chat import LastChatService

    svc = LastChatService(db, participant_svc)

    results = svc.get_recent_chats(current_user.user_id, limit)
    return {"recent_chats": results}
