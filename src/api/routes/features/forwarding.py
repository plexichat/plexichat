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


def _require_target_send_access(servers, user_id: int, target_conversation_id: int) -> None:
    """Fail closed when the forward target is a channel the caller cannot post to.

    Forwarding publishes the new message straight through
    ``messaging.send_message``. That core path does not apply the REST send
    route's ``messages.send`` gate (nor its timeout gate), and conversation
    participation alone is granted to every server member. A member who can
    view a read-only channel could therefore use /forward to plant forwarded
    content in a channel they are not allowed to post in.

    ``channels.view`` on the target is checked separately by ForwardingService;
    this adds the independent ``messages.send`` requirement. A conversation with
    no backing server channel is a genuine DM/group and needs no further gate.
    """
    if not servers:
        # The backing-channel lookup cannot be evaluated without the servers
        # module. A private-channel alias can exist in the DB independently of
        # that module, so skipping this gate would let any participant forward
        # into a channel whose messages.send grant was never checked. Deny
        # instead; a wired store whose reverse lookup finds no backing channel
        # still allows genuine DMs below.
        raise PermissionError("Cannot verify channel send access")
    lookup = getattr(servers, "get_channel_by_conversation", None)
    if not callable(lookup):
        raise PermissionError("Cannot verify channel send access")
    try:
        info = lookup(target_conversation_id)
    except Exception:
        raise PermissionError("Cannot verify channel send access")
    if info is None:
        return  # DM/group conversation: participation already gated it
    channel_id = info.get("id") if isinstance(info, dict) else None
    server_id = info.get("server_id") if isinstance(info, dict) else None
    if channel_id is None or server_id is None:
        raise PermissionError("Cannot verify channel send access")
    has_permission = getattr(servers, "has_permission", None)
    if not callable(has_permission):
        raise PermissionError("Cannot verify channel send access")
    try:
        allowed = bool(has_permission(user_id, server_id, "messages.send", channel_id))
    except Exception:
        raise PermissionError("Cannot verify channel send access")
    if not allowed:
        raise PermissionError("Cannot send messages to the target channel")


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
    servers_mod = api.get_servers()

    # A server member may view a read-only channel without being allowed to
    # post in it. The forward publish path bypasses the REST send route's
    # messages.send gate, so enforce it here (fail closed) before any forward
    # is created. channels.view on both sides is still enforced by the service.
    call_or_raise(
        _require_target_send_access,
        servers_mod,
        current_user.user_id,
        target_conversation_id,
    )

    # Pass the live servers module so the service can enforce the per-channel
    # channels.view gate on the source and target conversations. Participation
    # alone is granted to every server member, so forwarding would otherwise
    # let a member denied view on a private channel read/export its messages.
    svc = ForwardingService(db, messaging, participant_svc, servers_mod)

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
