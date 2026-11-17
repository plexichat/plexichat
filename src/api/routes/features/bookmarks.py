from fastapi import APIRouter, Depends, Query, HTTPException
from pydantic import BaseModel, Field, field_validator
from typing import Optional

import asyncio

import src.api as api
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.common import ErrorResponse, _reject_ui_text_chars
from src.core.events.snowflakes import stringify_snowflake_ids
from src.core.messaging.services.bookmarks import BookmarkService
from src.core.security.hardening import reject_control_characters, require_http_url
from .common import parse_id, raise_internal
import utils.logger as logger

router = APIRouter()

#: A bookmark label is UI text shown in the bookmarks modal, not a free-form
#: note. A short, bounded string keeps the event payload and the rendered card
#: predictable; the previous 100-char allowance was never used legitimately.
MAX_LABEL_LENGTH = 80
MAX_URL_LENGTH = 2048


class BookmarkRequest(BaseModel):
    message_id: str = Field(..., description="ID of the message to bookmark")
    conversation_id: str = Field(..., description="ID of the conversation")
    label: Optional[str] = Field(
        None, max_length=MAX_LABEL_LENGTH, description="Optional label"
    )
    url: Optional[str] = Field(
        None,
        max_length=MAX_URL_LENGTH,
        description="Optional http(s) deep link to the bookmarked message",
    )

    @field_validator("label")
    @classmethod
    def _no_ui_xss_chars(cls, value: Optional[str], info) -> Optional[str]:
        if value is None:
            return value
        # Strip first so trailing whitespace/controls cannot survive as a
        # deceptively "valid" label, then reject embedded control characters
        # (C0 + DEL) before the UI-schema check. Both helpers raise ValueError
        # subclasses, so FastAPI maps them to the usual 422 response.
        stripped = value.strip()
        if not stripped:
            return None
        reject_control_characters(stripped, info.field_name)
        return _reject_ui_text_chars(stripped, info.field_name)

    @field_validator("url")
    @classmethod
    def _safe_deep_link(cls, value: Optional[str], info) -> Optional[str]:
        if value is None:
            return value
        candidate = value.strip()
        if not candidate:
            return None
        reject_control_characters(candidate, info.field_name)
        # require_http_url rejects javascript:, data:, file:, credential-bearing
        # URLs and relative references in one place; bookmarks must never carry
        # a scheme that can execute in the client when the card is clicked.
        return require_http_url(candidate, info.field_name)


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
                # Only the validated http(s) deep link is ever dispatched.
                "url": bookmark.get("url"),
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
    responses={
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
    },
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
        db = api.get_db()
        bookmark_svc = BookmarkService(db, messaging)

    # M8 existence-oracle contract: nonexistent messages, conversation
    # mismatches, and non-participant callers all produce the IDENTICAL
    # 404 "Message not found" response. This mirrors the message-GET route
    # (messages_crud/retrieve.py), where a non-viewable message also yields
    # 404 rather than 403, so probing bookmark IDs cannot disclose whether
    # a message exists. Details stay server-side; never echo exception text.
    try:
        result = bookmark_svc.add_bookmark(
            user_id=current_user.user_id,
            message_id=message_id,
            conversation_id=conversation_id,
            label=body.label,
        )
    except (ValueError, PermissionError) as exc:
        logger.debug(
            "Bookmark create denied for user %s: %s",
            current_user.user_id,
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": 404, "message": "Message not found"}},
        )
    except Exception:
        logger.error(
            "Bookmark create failed for user %s", current_user.user_id, exc_info=True
        )
        raise_internal("Internal server error")
    # Fire-and-forget: notify our own websocket so bookmarks modal stays live.
    # Attach the already-validated deep link without mutating the service's
    # return value (it may be a shared/model object).
    event_payload = dict(result or {})
    if body.url and "url" not in event_payload:
        event_payload["url"] = body.url
    asyncio.create_task(
        _dispatch_bookmark_event("add", current_user.user_id, event_payload)
    )
    return {"success": True, "bookmark": stringify_snowflake_ids(result or {})}


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

    bookmark_svc = BookmarkService(db, messaging)
    # M8: deletion is scoped to (user_id, message_id) and stays idempotent
    # (always success) so it discloses nothing about message existence or
    # other users' bookmarks; no view check applies here.
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
                "url": None,
                "created_at": None,
                "message_content": None,
            },
        )
    )
    return {"success": True}


@router.get(
    "/bookmarks",
    summary="List your bookmarks",
    responses={
        401: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
    },
)
async def list_bookmarks(
    conversation_id: Optional[str] = None,
    limit: int = Query(50, ge=1, le=100),
    current_user: TokenInfo = Depends(get_current_user),
):
    messaging = api.get_messaging()
    db = api.get_db()

    bookmark_svc = BookmarkService(db, messaging)
    conv_id = parse_id(conversation_id, "conversation ID") if conversation_id else None
    try:
        results = bookmark_svc.get_bookmarks(current_user.user_id, conv_id, limit)
    except (PermissionError, ValueError):
        # M8: same oracle contract as bookmark creation — a conversation
        # filter the caller cannot view yields the identical 404 rather
        # than 403/400, so conversation IDs cannot be probed via this
        # endpoint. Never echo exception text to the client.
        logger.debug("Bookmark list denied for user %s", current_user.user_id)
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": 404, "message": "Conversation not found"}},
        )
    return {"bookmarks": stringify_snowflake_ids(results or [])}
