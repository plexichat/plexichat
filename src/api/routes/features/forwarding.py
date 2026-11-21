from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

import src.api as api
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.common import ErrorResponse
from src.core.events.snowflakes import stringify_snowflake_ids
from src.core.messaging.services.forwarding import ForwardingService
from src.api.routes.users.helpers import require_user_token
from .common import parse_id, call_or_raise, resolve_participant_service

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
    require_user_token(current_user)
    message_id = parse_id(body.message_id, "message ID")
    target_conversation_id = parse_id(
        body.target_conversation_id, "target conversation ID"
    )

    db = api.get_db()
    messaging = api.get_messaging()
    participant_svc = resolve_participant_service(messaging)

    # Pass the live servers module so the service can enforce the per-channel
    # channels.view gate on the source and target conversations. Participation
    # alone is granted to every server member, so forwarding would otherwise
    # let a member denied view on a private channel read/export its messages.
    svc = ForwardingService(db, messaging, participant_svc, api.get_servers())

    result = call_or_raise(
        svc.forward_message,
        current_user.user_id,
        message_id,
        target_conversation_id,
    )
    payload = stringify_snowflake_ids(result)
    # ``forwarded_by`` names the forwarding user's snowflake but does not
    # match the central ``id``/``*_id``/``*_ids`` rule, so stringify it here
    # the way the neighbouring routes str() their ``*_by`` actor fields.
    if payload.get("forwarded_by") is not None:
        payload["forwarded_by"] = str(payload["forwarded_by"])
    return {"success": True, "forward": payload}
