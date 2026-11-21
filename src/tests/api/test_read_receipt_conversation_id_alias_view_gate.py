"""SECURITY: read-receipt routes must not accept a channel's linked
conversation id as an unguarded alias for the channel.

Read-receipt routes resolve the URL ``channel_id`` to a conversation with a
best-effort ``servers.get_channel(cid, user)`` and, when that returns ``None``,
fall back to treating the id itself as a DM conversation id. ``get_channel``
returns ``None`` both for a genuine DM *and* for a server channel denied by
``channels.view``. A server channel's linked ``conversation_id`` has no
``srv_channels`` row at all, so a member denied view on a private channel can
pass that id as ``/channels/{id}/messages/unread`` (or last-read / ack) and
skip the resolver entirely. The downstream unread/read-state lookup only checks
conversation participation, which every server member has regardless of
``channels.view``.

Fix: gate the addressed id membership-agnostically for both spellings (channel
id and linked conversation id) before any read-state lookup, exactly like the
shared message retrieve/delete gate.

Run with: pytest src/tests/api/test_read_receipt_conversation_id_alias_view_gate.py -q -n 0
"""

import asyncio

import pytest
from fastapi import HTTPException

import src.api as api
from src.api.routes.messages import read_receipts as rr

pytestmark = [pytest.mark.unit, pytest.mark.security]

CHANNEL = 8201  # real (private) server channel id
CONV = 8202  # its linked conversation id -- the URL alias used by the attack
OTHER = 9999  # unrelated id / genuine DM
USER = 42


class _User:
    user_id = USER
    token_type = "user"
    username = "u"


class _Channel:
    conversation_id = CONV
    server_id = 1
    channel_type = "text"


class _Servers:
    def __init__(self, *, view_allowed: bool):
        self.view_allowed = view_allowed
        self.get_channel_calls = []
        self.reverse_calls = []

    def get_channel(self, channel_id, user_id):
        self.get_channel_calls.append((channel_id, user_id))
        if channel_id == CHANNEL and self.view_allowed:
            return _Channel()
        return None

    def channel_exists(self, channel_id):
        return channel_id == CHANNEL

    def get_channel_by_conversation(self, conversation_id):
        self.reverse_calls.append(conversation_id)
        if conversation_id == CONV:
            return {"id": CHANNEL, "server_id": 1}
        return None

    def get_channel_conversation_id(self, channel_id):
        return CONV if channel_id == CHANNEL else None


class _Messaging:
    def __init__(self):
        self.unread_calls = []

    def get_unread_count(self, user_id, conversation_id=None):
        self.unread_calls.append((user_id, conversation_id))
        # Only the private channel's conversation actually has messages;
        # a DM/unrelated id legitimately resolves to an empty count.
        count = 7 if conversation_id == CONV else 0
        return {conversation_id: count}


def _wire(monkeypatch, servers, messaging):
    monkeypatch.setattr(api, "get_servers", lambda: servers)
    monkeypatch.setattr(api, "get_messaging", lambda: messaging)


def _unread(channel_id):
    return asyncio.run(rr.get_unread_count(str(channel_id), _User()))


class TestReadReceiptConversationIdAliasGate:
    def test_conversation_alias_denied_without_view(self, monkeypatch):
        """Core leak: alias used as channel_id must not reach the unread lookup."""
        servers = _Servers(view_allowed=False)
        messaging = _Messaging()
        _wire(monkeypatch, servers, messaging)

        with pytest.raises(HTTPException) as ei:
            _unread(CONV)

        assert ei.value.status_code == 404
        assert messaging.unread_calls == [], "private unread count was disclosed"
        # The real backing channel must have been gated, not the alias string.
        assert (CHANNEL, USER) in servers.get_channel_calls
        assert CONV in servers.reverse_calls

    def test_conversation_alias_allowed_with_view(self, monkeypatch):
        """Control: a viewer who can see the backing channel may read unread."""
        servers = _Servers(view_allowed=True)
        messaging = _Messaging()
        _wire(monkeypatch, servers, messaging)

        assert _unread(CONV).unread_count == 7
        assert (USER, CONV) in messaging.unread_calls

    def test_real_channel_id_denied_without_view(self, monkeypatch):
        """Control: the addressed-id gate still denies the real channel id."""
        servers = _Servers(view_allowed=False)
        messaging = _Messaging()
        _wire(monkeypatch, servers, messaging)

        with pytest.raises(HTTPException) as ei:
            _unread(CHANNEL)

        assert ei.value.status_code == 404
        assert messaging.unread_calls == []

    def test_genuine_dm_not_gated(self, monkeypatch):
        """Control: an id with no backing channel stays a DM (participation gate)."""
        servers = _Servers(view_allowed=False)
        messaging = _Messaging()
        _wire(monkeypatch, servers, messaging)

        assert _unread(OTHER).unread_count == 0
        assert (USER, OTHER) in messaging.unread_calls

    def test_broken_reverse_lookup_fails_closed(self, monkeypatch):
        """A failing reverse lookup must never be treated as 'DM, allow'."""
        servers = _Servers(view_allowed=False)
        messaging = _Messaging()

        def _raises(_cid):
            raise RuntimeError("lookup down")

        servers.get_channel_by_conversation = _raises
        _wire(monkeypatch, servers, messaging)

        with pytest.raises(HTTPException) as ei:
            _unread(CONV)

        assert ei.value.status_code == 404
        assert messaging.unread_calls == []
