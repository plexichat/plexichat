"""SECURITY: get-message-by-ID must enforce live server-channel view access.

``RetrieveMixin.get_message`` (GET /channels/{channel_id}/messages/{message_id})
relies on ``messaging.get_message(user_id, mid)`` for its access decision. For
server channels that only proves server *membership*: ``is_participant`` grants
every member of the server participation in its channel conversations, regardless
of the per-channel ``channels.view`` permission. The URL channel is then only
checked for *binding* (does it map to the message's conversation), never for
whether the caller may view it.

Pin/unpin routes already added the missing live ``get_channel`` gate for exactly
this reason. The read route did not, so a member denied view on a private channel
could still fetch any message in it by id -- including attachment metadata and
viewer-bound signed attachment URLs.

Run with: pytest src/tests/api/test_message_get_channel_view_gate.py -q -n 0
"""

import pytest
from fastapi import HTTPException

import src.api as api
import src.api.routes.messages.messages_crud.retrieve as route
from src.core.auth.models import AccountType, TokenInfo

pytestmark = [pytest.mark.unit, pytest.mark.security]

CHANNEL_ID = 7001
CONV_ID = 7002
MESSAGE_ID = 7003
VIEWER_ID = 42
SENTINEL = object()


def _viewer() -> TokenInfo:
    return TokenInfo(
        valid=True,
        token_type="user",
        account_id=VIEWER_ID,
        user_id=VIEWER_ID,
        session_id=None,
        permissions={},
        rate_limit_tier="standard",
        expires_at=None,
        username="viewer",
        account_type=AccountType.USER,
    )


class _Message:
    id = MESSAGE_ID
    conversation_id = CONV_ID
    author_id = 99
    content = "secret-private-channel-content"
    created_at = 1
    attachments = []


class _Messaging:
    """Stand-in whose participant gate is satisfied (server member)."""

    def __init__(self):
        self.calls = []

    def get_message(self, user_id, message_id):
        self.calls.append((user_id, message_id))
        return _Message()


class _Servers:
    """Fake servers module with an authoritative per-channel view decision."""

    def __init__(self, *, view_allowed: bool, probe_raises: bool = False):
        self.view_allowed = view_allowed
        self.probe_raises = probe_raises
        self.get_channel_calls = []

    def get_channel_conversation_id(self, channel_id):
        return CONV_ID

    def channel_exists(self, channel_id):
        if self.probe_raises:
            raise RuntimeError("existence probe unavailable")
        return True  # it IS a server channel, just not viewable

    def get_channel(self, channel_id, user_id):
        self.get_channel_calls.append((channel_id, user_id))
        return object() if self.view_allowed else None


def _wire(monkeypatch, servers) -> _Messaging:
    messaging = _Messaging()
    monkeypatch.setattr(api, "get_messaging", lambda: messaging)
    monkeypatch.setattr(api, "get_servers", lambda: servers)
    monkeypatch.setattr(api, "get_auth", lambda: None)
    monkeypatch.setattr(api, "get_media", lambda: None)
    monkeypatch.setattr(api, "get_db", lambda: None)
    monkeypatch.setattr(route, "_message_to_response", lambda *a, **k: SENTINEL)
    return messaging


@pytest.mark.asyncio
async def test_message_by_id_denied_when_channel_view_revoked(monkeypatch):
    """The core leak: member is a participant but cannot view the channel."""
    servers = _Servers(view_allowed=False)
    messaging = _wire(monkeypatch, servers)

    with pytest.raises(HTTPException) as exc:
        await route.RetrieveMixin().get_message(
            str(CHANNEL_ID), str(MESSAGE_ID), current_user=_viewer()
        )

    assert exc.value.status_code == 404
    # The live view gate actually ran; this is not a generic failure.
    assert servers.get_channel_calls == [(CHANNEL_ID, VIEWER_ID)]
    # The message body was resolved by the participant gate but never returned.
    assert messaging.calls == [(VIEWER_ID, MESSAGE_ID)]


@pytest.mark.asyncio
async def test_message_by_id_allowed_when_channel_view_granted(monkeypatch):
    """Control: a viewer WITH channels.view still gets the message."""
    servers = _Servers(view_allowed=True)
    _wire(monkeypatch, servers)

    result = await route.RetrieveMixin().get_message(
        str(CHANNEL_ID), str(MESSAGE_ID), current_user=_viewer()
    )

    assert result is SENTINEL
    assert servers.get_channel_calls == [(CHANNEL_ID, VIEWER_ID)]


@pytest.mark.asyncio
async def test_message_by_id_fails_closed_when_existence_probe_errors(monkeypatch):
    """If we cannot prove the id is a DM, deny rather than serve the body."""
    servers = _Servers(view_allowed=False, probe_raises=True)
    _wire(monkeypatch, servers)

    with pytest.raises(HTTPException) as exc:
        await route.RetrieveMixin().get_message(
            str(CHANNEL_ID), str(MESSAGE_ID), current_user=_viewer()
        )

    assert exc.value.status_code == 404
