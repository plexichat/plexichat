from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from typing import Optional

import utils.logger as logger
import src.api as api
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.common import ErrorResponse
from src.core.events.snowflakes import stringify_snowflake_ids
from src.core.messaging.services.last_chat import LastChatService
from src.api.routes.users.helpers import require_user_token
from .common import parse_id, raise_forbidden, raise_internal

router = APIRouter()

# Scroll position is a pixel offset used only to restore the client viewport.
# It is persisted in ``user_last_chat.scroll_position`` and mirrored into the
# per-user cache. The request schema used to accept any integer (including
# negatives and values past SQLite's 64-bit INTEGER range), so a client could
# either persist a multi-kilobyte number or drive the upsert into an
# ``OverflowError`` that surfaced as a 500. Keep it a small, non-negative
# offset at the boundary.
MAX_SCROLL_POSITION = 100_000_000


class LastChatRequest(BaseModel):
    conversation_id: str = Field(..., description="Last active conversation ID")
    last_message_id: Optional[str] = Field(None, description="Last visible message ID")
    scroll_position: Optional[int] = Field(
        None,
        ge=0,
        le=MAX_SCROLL_POSITION,
        description="Scroll position for restoration",
    )


@router.put(
    "/users/@me/last-chat",
    summary="Save last active chat",
    responses={400: {"model": ErrorResponse}, 401: {"model": ErrorResponse}},
)
async def save_last_chat(
    body: LastChatRequest, current_user: TokenInfo = Depends(get_current_user)
):
    require_user_token(current_user)
    # Route-level id grammar: bare ``int()`` accepted negatives, zero,
    # out-of-range values, Unicode digit homoglyphs and Python forms such as
    # ``1_2``. Use the shared snowflake guard (strict ASCII digits + 64-bit
    # range) so a malformed id is rejected with the route's 400 contract
    # before it can reach the participant check or the upsert.
    conversation_id = parse_id(body.conversation_id, "conversation ID")
    last_message_id = (
        parse_id(body.last_message_id, "message ID") if body.last_message_id else None
    )

    db = api.get_db()
    messaging = api.get_messaging()
    participant_svc = (
        getattr(messaging, "_participant_svc", None) if messaging else None
    )

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
        return {"success": True, "last_chat": stringify_snowflake_ids(result)}
    except PermissionError:
        raise_forbidden("Access denied")
    except Exception:
        logger.error("Failed to save last chat", exc_info=True)
        raise_internal("Failed to save last chat state")


@router.get(
    "/users/@me/last-chat",
    summary="Get last active chat",
    responses={401: {"model": ErrorResponse}},
)
async def get_last_chat(
    current_user: TokenInfo = Depends(get_current_user),
):
    require_user_token(current_user)
    db = api.get_db()
    messaging = api.get_messaging()
    participant_svc = (
        getattr(messaging, "_participant_svc", None) if messaging else None
    )

    svc = LastChatService(db, participant_svc)

    result = svc.get_last_chat(current_user.user_id)
    if not result:
        return {"last_chat": None}
    return {"last_chat": stringify_snowflake_ids(result)}


@router.get(
    "/users/@me/recent-chats",
    summary="Get recent chat history",
    responses={401: {"model": ErrorResponse}},
)
async def get_recent_chats(
    limit: int = Query(10, ge=1, le=50),
    current_user: TokenInfo = Depends(get_current_user),
):
    require_user_token(current_user)
    db = api.get_db()
    messaging = api.get_messaging()
    participant_svc = (
        getattr(messaging, "_participant_svc", None) if messaging else None
    )

    svc = LastChatService(db, participant_svc)

    results = svc.get_recent_chats(current_user.user_id, limit)
    return {"recent_chats": stringify_snowflake_ids(results or [])}
