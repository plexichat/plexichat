"""SECURITY: message delete paths must enforce live server-channel view access.

``DeleteMixin.delete_message`` / ``bulk_delete_messages`` resolve the target
with ``messaging.get_message(user_id, mid)`` and then only check that the URL
channel is *bound* to the message's conversation. For server channels that is
not an authorization boundary: ``is_participant`` grants every server member
participation in its channel conversations regardless of the per-channel
``channels.view`` permission. The GET-by-id route already added the missing
live ``get_channel`` gate for exactly this reason; the delete routes never
did, so a member denied view on a private channel could still delete messages
in it -- their own, and anyone's when they hold a stale OWNER/ADMIN
conversation-participant role (the channel creator is seeded as OWNER).

These tests pin the boundary: a viewer WITH channels.view may delete, a member
denied view may not, and the live check runs before any delete/write.

Run with: pytest src/tests/api/test_message_delete_channel_view_gate.py -q -n 0
"""

import pytest
from fastapi import HTTPException

import src.api as api
import src.api.routes.messages.messages_crud.broadcast as broadcast
import src.api.routes.messages.messages_crud.delete as route
import src.api.routes.messages.helpers as helpers
from src.core.auth.models import AccountType, TokenInfo

pytestmark = [pytest.mark.unit, pytest.mark.security]

CHANNEL_ID = 7001
CONV_ID = 7002
MESSAGE_ID = 7003
OTHER_MESSAGE_ID = 7004
VIEWER_ID = 42
AUTHOR_ID = 99
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
        username="member-denied-view",
        account_type=AccountType.USER,
    )


class _Message:
    id = MESSAGE_ID
    conversation_id = CONV_ID
    author_id = AUTHOR_ID
    content = "secret-private-channel-content"
    created_at = 1
    attachments = []
    metadata = None


class _Messaging:
    """Stand-in whose participant gate is satisfied (server member)."""

    def __init__(self):
        self.get_message_calls = []
        self.delete_calls = []
        self.bulk_delete_calls = []

    def get_message(self, user_id, message_id):
        self.get_message_calls.append((user_id, message_id))
        return _Message()

    def delete_message(self, user_id, message_id, hard_delete=False):
        self.delete_calls.append((user_id, message_id, hard_delete))
        return True

    def delete_messages_bulk(self, user_id, channel_id, message_ids):
        self.bulk_delete_calls.append((user_id, channel_id, tuple(message_ids)))
        return list(message_ids)


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


class _Body:
    def __init__(self, ids):
        self.message_ids = ids


def _wire(monkeypatch, servers) -> _Messaging:
    messaging = _Messaging()
    monkeypatch.setattr(api, "get_messaging", lambda: messaging)
    monkeypatch.setattr(api, "get_servers", lambda: servers)
    monkeypatch.setattr(api, "get_auth", lambda: None)
    monkeypatch.setattr(api, "get_media", lambda: None)
    monkeypatch.setattr(api, "get_db", lambda: None)
    monkeypatch.setattr(api, "get_polls", lambda: None)
    monkeypatch.setattr(api, "get_reactions", lambda: None)
    monkeypatch.setattr(helpers, "_message_to_response", lambda *a, **k: SENTINEL)

    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr(
        broadcast.BroadcastMixin, "_broadcast_message_update", _noop, raising=False
    )
    monkeypatch.setattr(
        broadcast.BroadcastMixin, "_broadcast_message_delete", _noop, raising=False
    )
    return messaging


@pytest.mark.asyncio
async def test_delete_message_denied_when_channel_view_revoked(monkeypatch):
    """Core leak: member is a participant but cannot view the channel."""
    servers = _Servers(view_allowed=False)
    messaging = _wire(monkeypatch, servers)

    with pytest.raises(HTTPException) as exc:
        await route.DeleteMixin().delete_message(
            str(CHANNEL_ID), str(MESSAGE_ID), current_user=_viewer()
        )

    assert exc.value.status_code == 404
    assert servers.get_channel_calls == [(CHANNEL_ID, VIEWER_ID)]
    # The gate ran before any mutation: the message was never deleted.
    assert messaging.delete_calls == []


@pytest.mark.asyncio
async def test_delete_message_allowed_when_channel_view_granted(monkeypatch):
    """Control: a viewer WITH channels.view can still delete."""
    servers = _Servers(view_allowed=True)
    messaging = _wire(monkeypatch, servers)

    result = await route.DeleteMixin().delete_message(
        str(CHANNEL_ID), str(MESSAGE_ID), current_user=_viewer()
    )

    assert result.success is True
    assert servers.get_channel_calls == [(CHANNEL_ID, VIEWER_ID)]
    assert messaging.delete_calls == [(VIEWER_ID, MESSAGE_ID, False)]


@pytest.mark.asyncio
async def test_bulk_delete_denied_when_channel_view_revoked(monkeypatch):
    """The bulk path must not be a bypass of the single-delete gate."""
    servers = _Servers(view_allowed=False)
    messaging = _wire(monkeypatch, servers)

    with pytest.raises(HTTPException) as exc:
        await route.DeleteMixin().bulk_delete_messages(
            str(CHANNEL_ID),
            body=_Body([MESSAGE_ID, OTHER_MESSAGE_ID]),
            current_user=_viewer(),
        )

    assert exc.value.status_code == 404
    assert servers.get_channel_calls == [(CHANNEL_ID, VIEWER_ID)]
    assert messaging.bulk_delete_calls == []


@pytest.mark.asyncio
async def test_bulk_delete_allowed_when_channel_view_granted(monkeypatch):
    """Control: a viewer WITH channels.view can bulk-delete."""
    servers = _Servers(view_allowed=True)
    messaging = _wire(monkeypatch, servers)

    result = await route.DeleteMixin().bulk_delete_messages(
        str(CHANNEL_ID),
        body=_Body([MESSAGE_ID, OTHER_MESSAGE_ID]),
        current_user=_viewer(),
    )

    assert result.success is True
    # The gate (plus the bulk event's recipient lookup) resolves the channel;
    # what matters is that the live view check ran before any write.
    assert (CHANNEL_ID, VIEWER_ID) in servers.get_channel_calls
    assert messaging.bulk_delete_calls == [
        (VIEWER_ID, CHANNEL_ID, (MESSAGE_ID, OTHER_MESSAGE_ID))
    ]


@pytest.mark.asyncio
async def test_delete_fails_closed_when_existence_probe_errors(monkeypatch):
    """If we cannot prove the id is a DM, deny rather than mutate."""
    servers = _Servers(view_allowed=False, probe_raises=True)
    messaging = _wire(monkeypatch, servers)

    with pytest.raises(HTTPException) as exc:
        await route.DeleteMixin().delete_message(
            str(CHANNEL_ID), str(MESSAGE_ID), current_user=_viewer()
        )

    assert exc.value.status_code == 404
    assert messaging.delete_calls == []
