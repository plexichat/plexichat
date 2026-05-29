from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from typing import Optional

import src.api as api
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.common import ErrorResponse
from .common import parse_id, call_or_raise

router = APIRouter()


class ScheduledMessageRequest(BaseModel):
    conversation_id: str = Field(..., description="Target conversation ID")
    content: str = Field(
        ..., min_length=1, max_length=4000, description="Message content"
    )
    scheduled_at: int = Field(..., description="Timestamp (ms) when to send")


@router.post(
    "/scheduled-messages",
    summary="Schedule a message",
    responses={400: {"model": ErrorResponse}, 401: {"model": ErrorResponse}},
)
async def create_scheduled_message(
    body: ScheduledMessageRequest, current_user: TokenInfo = Depends(get_current_user)
):
    conversation_id = parse_id(body.conversation_id, "conversation ID")

    db = api.get_db()
    messaging = api.get_messaging()
    participant_svc = (
        getattr(messaging, "_participant_svc", None) if messaging else None
    )

    from src.core.messaging.services.scheduled import ScheduledMessageService

    svc = ScheduledMessageService(db, participant_svc)

    result = call_or_raise(
        svc.create_scheduled_message,
        user_id=current_user.user_id,
        conversation_id=conversation_id,
        content=body.content,
        scheduled_at=body.scheduled_at,
    )
    return {"success": True, "scheduled_message": result}


@router.get(
    "/scheduled-messages",
    summary="List your scheduled messages",
    responses={401: {"model": ErrorResponse}},
)
async def list_scheduled_messages(
    conversation_id: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = Query(50, ge=1, le=100),
    current_user: TokenInfo = Depends(get_current_user),
):
    db = api.get_db()
    from src.core.messaging.services.scheduled import ScheduledMessageService

    svc = ScheduledMessageService(db)
    conv_id = int(conversation_id) if conversation_id else None
    results = svc.list_scheduled_messages(current_user.user_id, conv_id, status, limit)
    return {"scheduled_messages": results}


@router.delete(
    "/scheduled-messages/{scheduled_id}",
    summary="Cancel a scheduled message",
    responses={400: {"model": ErrorResponse}, 401: {"model": ErrorResponse}},
)
async def cancel_scheduled_message(
    scheduled_id: str, current_user: TokenInfo = Depends(get_current_user)
):
    sid = parse_id(scheduled_id, "scheduled message ID")

    db = api.get_db()
    from src.core.messaging.services.scheduled import ScheduledMessageService

    svc = ScheduledMessageService(db)

    call_or_raise(svc.cancel_scheduled_message, sid, current_user.user_id)
    return {"success": True}
