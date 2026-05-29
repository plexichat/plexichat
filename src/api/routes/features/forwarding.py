from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

import src.api as api
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.common import ErrorResponse
from .common import parse_id, call_or_raise

router = APIRouter()


class ForwardMessageRequest(BaseModel):
    message_id: str = Field(..., description="ID of the message to forward")
    target_conversation_id: str = Field(..., description="Target conversation ID")


@router.post(
    "/forward",
    summary="Forward a message",
    responses={400: {"model": ErrorResponse}, 401: {"model": ErrorResponse}},
)
async def forward_message(
    body: ForwardMessageRequest, current_user: TokenInfo = Depends(get_current_user)
):
    message_id = parse_id(body.message_id, "message ID")
    target_conversation_id = parse_id(
        body.target_conversation_id, "target conversation ID"
    )

    db = api.get_db()
    messaging = api.get_messaging()
    participant_svc = (
        getattr(messaging, "_participant_svc", None) if messaging else None
    )

    from src.core.messaging.services.forwarding import ForwardingService

    svc = ForwardingService(db, messaging, participant_svc)

    result = call_or_raise(
        svc.forward_message,
        current_user.user_id,
        message_id,
        target_conversation_id,
    )
    return {"success": True, "forward": result}
