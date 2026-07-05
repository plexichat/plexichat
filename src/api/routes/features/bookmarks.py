from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from typing import Optional

import asyncio

import src.api as api
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.common import ErrorResponse
from .common import parse_id, call_or_raise, raise_internal
import utils.logger as logger

router = APIRouter()


class BookmarkRequest(BaseModel):
    message_id: str = Field(..., description="ID of the message to bookmark")
    conversation_id: str = Field(..., description="ID of the conversation")
    label: Optional[str] = Field(None, max_length=100, description="Optional label")


async def _dispatch_bookmark_event(event_type: str, user_id: int, bookmark: dict):
    """Dispatch bookmark add/remove event to the owning user via WebSocket.

    Bookmarks are per-user, so the event is only sent to the user who
    created/removed it. This avoids broadcasting private data to other
    members of a conversation.
    """
    try:
        from src.api.websocket import get_dispatcher, is_setup as ws_is_setup
        from src.core.events.models import Event
        from src.core.events.types import EventType

        if not ws_is_setup():
            return

        dispatcher = get_dispatcher()
        if not dispatcher:
            return

        evt_type = (
            EventType.MESSAGE_BOOKMARK_ADD
            if event_type == "add"
            else EventType.MESSAGE_BOOKMARK_REMOVE
        )
        event = Event(
            event_type=evt_type,
            data={
                "user_id": str(user_id),
                "message_id": str(bookmark.get("message_id", "")),
                "conversation_id": str(bookmark.get("conversation_id", "")),
                "bookmark_id": str(bookmark.get("id", "")),
                "label": bookmark.get("label"),
                "created_at": bookmark.get("created_at"),
                "message_content": bookmark.get("message_content"),
            },
        )
        await dispatcher.dispatch_event(event, [user_id])
    except Exception as e:
        logger.debug(f"Failed to dispatch bookmark event for user {user_id}: {e}")


@router.post(
    "/bookmarks",
    summary="Bookmark a message",
    responses={400: {"model": ErrorResponse}, 401: {"model": ErrorResponse}},
)
async def add_bookmark(
    body: BookmarkRequest, current_user: TokenInfo = Depends(get_current_user)
):
    messaging = api.get_messaging()
    if not messaging:
        raise_internal("Internal server error")

    message_id = parse_id(body.message_id, "message ID")
    conversation_id = parse_id(body.conversation_id, "conversation ID")

    bookmark_svc = (
        messaging._bookmark_svc if hasattr(messaging, "_bookmark_svc") else None
    )
    if not bookmark_svc:
        from src.core.messaging.services.bookmarks import BookmarkService

        db = api.get_db()
        bookmark_svc = BookmarkService(db, messaging)

    result = call_or_raise(
        bookmark_svc.add_bookmark,
        user_id=current_user.user_id,
        message_id=message_id,
        conversation_id=conversation_id,
        label=body.label,
    )
    # Fire-and-forget: notify our own websocket so bookmarks modal stays live
    asyncio.create_task(
        _dispatch_bookmark_event("add", current_user.user_id, result or {})
    )
    return {"success": True, "bookmark": result}


@router.delete(
    "/bookmarks/{message_id}",
    summary="Remove a bookmark",
    responses={401: {"model": ErrorResponse}},
)
async def remove_bookmark(
    message_id: str, current_user: TokenInfo = Depends(get_current_user)
):
    mid = parse_id(message_id, "message ID")
    messaging = api.get_messaging()
    db = api.get_db()
    from src.core.messaging.services.bookmarks import BookmarkService

    bookmark_svc = BookmarkService(db, messaging)
    bookmark_svc.remove_bookmark(current_user.user_id, mid)
    # Fire-and-forget: notify our own websocket so bookmarks modal can remove it
    asyncio.create_task(
        _dispatch_bookmark_event(
            "remove",
            current_user.user_id,
            {
                "message_id": str(mid),
                "id": str(mid),
                "conversation_id": "",
                "label": None,
                "created_at": None,
                "message_content": None,
            },
        )
    )
    return {"success": True}


@router.get(
    "/bookmarks",
    summary="List your bookmarks",
    responses={401: {"model": ErrorResponse}},
)
async def list_bookmarks(
    conversation_id: Optional[str] = None,
    limit: int = Query(50, ge=1, le=100),
    current_user: TokenInfo = Depends(get_current_user),
):
    messaging = api.get_messaging()
    db = api.get_db()
    from src.core.messaging.services.bookmarks import BookmarkService

    bookmark_svc = BookmarkService(db, messaging)
    conv_id = int(conversation_id) if conversation_id else None
    results = bookmark_svc.get_bookmarks(current_user.user_id, conv_id, limit)
    return {"bookmarks": results}
