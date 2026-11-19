from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from typing import Optional

import src.api as api
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.common import ErrorResponse
from src.core.events.snowflakes import stringify_snowflake_ids
from src.core.messaging.services.scheduled import ScheduledMessageService
from src.core.security.hardening import reject_control_characters
from src.api.routes.users.helpers import require_user_token
from .common import parse_id, call_or_raise, resolve_participant_service

router = APIRouter()

MAX_CONTENT_LENGTH = 4000
MAX_SCHEDULED_AT_MS = 4102444800000  # 2100-01-01 in milliseconds
VALID_SCHEDULE_STATUSES = frozenset(
    {"pending", "scheduled", "sent", "cancelled", "failed"}
)


class ScheduledMessageRequest(BaseModel):
    conversation_id: str = Field(..., description="Target conversation ID")
    content: str = Field(
        ...,
        min_length=1,
        max_length=MAX_CONTENT_LENGTH,
        description="Message content",
    )
    scheduled_at: int = Field(
        ...,
        ge=1,
        le=MAX_SCHEDULED_AT_MS,
        description="Timestamp (ms) when to send",
    )

    @field_validator("content")
    @classmethod
    def _safe_content(cls, value: str, info):
        if not value or not value.strip():
            raise ValueError("content must not be blank")
        # Newlines and tabs are legitimate in a scheduled message; NUL, DEL
        # and the remaining C0 controls are never valid in stored text.
        if any(ord(ch) < 32 and ch not in "\t\n\r" for ch in value) or "\x7f" in value:
            raise ValueError("content contains control characters")
        reject_control_characters(
            value.replace("\t", "").replace("\n", "").replace("\r", ""), info.field_name
        )
        return value


@router.post(
    "/scheduled-messages",
    summary="Schedule a message",
    responses={400: {"model": ErrorResponse}, 401: {"model": ErrorResponse}},
)
async def create_scheduled_message(
    body: ScheduledMessageRequest, current_user: TokenInfo = Depends(get_current_user)
):
    require_user_token(current_user)
    conversation_id = parse_id(body.conversation_id, "conversation ID")

    db = api.get_db()
    messaging = api.get_messaging()
    participant_svc = resolve_participant_service(messaging)

    svc = ScheduledMessageService(db, participant_svc)

    result = call_or_raise(
        svc.create_scheduled_message,
        user_id=current_user.user_id,
        conversation_id=conversation_id,
        content=body.content,
        scheduled_at=body.scheduled_at,
    )
    return {"success": True, "scheduled_message": stringify_snowflake_ids(result)}


@router.get(
    "/scheduled-messages",
    summary="List your scheduled messages",
    responses={401: {"model": ErrorResponse}},
)
async def list_scheduled_messages(
    conversation_id: Optional[str] = None,
    status: Optional[str] = Query(None, max_length=32),
    limit: int = Query(50, ge=1, le=100),
    current_user: TokenInfo = Depends(get_current_user),
):
    require_user_token(current_user)
    if status is not None and status not in VALID_SCHEDULE_STATUSES:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Invalid schedule status"}},
        )
    db = api.get_db()

    svc = ScheduledMessageService(db)
    conv_id = parse_id(conversation_id, "conversation ID") if conversation_id else None
    results = svc.list_scheduled_messages(current_user.user_id, conv_id, status, limit)
    return {"scheduled_messages": stringify_snowflake_ids(results or [])}


@router.delete(
    "/scheduled-messages/{scheduled_id}",
    summary="Cancel a scheduled message",
    responses={400: {"model": ErrorResponse}, 401: {"model": ErrorResponse}},
)
async def cancel_scheduled_message(
    scheduled_id: str, current_user: TokenInfo = Depends(get_current_user)
):
    require_user_token(current_user)
    sid = parse_id(scheduled_id, "scheduled message ID")

    db = api.get_db()

    svc = ScheduledMessageService(db)

    call_or_raise(svc.cancel_scheduled_message, sid, current_user.user_id)
    return {"success": True}
