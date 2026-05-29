from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from typing import Optional

import src.api as api
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.common import ErrorResponse
from .common import parse_id, call_or_raise, raise_internal

router = APIRouter()


class BookmarkRequest(BaseModel):
    message_id: str = Field(..., description="ID of the message to bookmark")
    conversation_id: str = Field(..., description="ID of the conversation")
    label: Optional[str] = Field(None, max_length=100, description="Optional label")


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
