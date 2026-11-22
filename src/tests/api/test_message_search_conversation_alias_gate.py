"""SECURITY: channel message search must gate a conversation-id URL alias.

``GET /channels/{id}/messages/search`` resolves the channel with
``get_channel(url_id)``/``channel_exists(url_id)``. Message routes also accept
a server channel's linked ``conversation_id`` as a URL alias. Those probes
only know channel ids and report "not a channel" for the alias, so the route
used to treat it as a DM and SKIP its live ``messages.read`` gate. A member
denied read on a private channel could then search its messages simply by
using the linked conversation id in the URL -- and, because the alias and the
channel resolve to the same server-side conversation id, a still-warm cached
result page was served on repeat requests.

These tests pin that the alias is reverse-resolved membership-agnostically and
gated on the REAL backing channel before any cached body is served, failing
closed when the grant cannot be established.

Run with: pytest src/tests/api/test_message_search_conversation_alias_gate.py -q -n 0
"""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import src.api as api
import src.api.routes.messages.search as route
from src.core.auth.models import AccountType, TokenInfo

pytestmark = [pytest.mark.unit, pytest.mark.security]

CHANNEL_ID = 9411
CONV_ID = 9412  # linked conversation id -- the URL alias
SERVER_ID = 9413
VIEWER_ID = 61


def _viewer():
    return TokenInfo(
        valid=True,
        token_type="user",
        account_id=VIEWER_ID,
        user_id=VIEWER_ID,
        session_id=None,
        permissions={},
        rate_limit_tier="standard",
        expires_at=None,
        username="read_denied_member",
        account_type=AccountType.USER,
    )


class _Messaging:
    def is_participant(self, conversation_id, user_id):
        # Server membership alone grants participation in every channel.
        return conversation_id == CONV_ID and user_id == VIEWER_ID


class _Servers:
    """Alias-aware store: get_channel/channel_exists only know real channel ids."""

    def __init__(self, *, messages_read=False, break_permission=False):
        self.messages_read = messages_read
        self.break_permission = break_permission
        self.permission_calls = []

    def channel_exists(self, channel_id):
        return channel_id == CHANNEL_ID

    def get_channel(self, channel_id, user_id):
        # A conversation-id alias is NOT a channel id. Only the real channel
        # resolves, and only to a caller who can view it.
        if channel_id == CHANNEL_ID:
            return SimpleNamespace(
                id=CHANNEL_ID,
                server_id=SERVER_ID,
                conversation_id=CONV_ID,
            )
        return None

    def get_channel_by_conversation(self, conversation_id):
        if conversation_id == CONV_ID:
            return {"id": CHANNEL_ID, "server_id": SERVER_ID}
        return None

    def has_permission(self, user_id, server_id, permission, channel_id=None):
        self.permission_calls.append((user_id, server_id, permission, channel_id))
        if self.break_permission:
            raise RuntimeError("permission store unavailable")
        return self.messages_read


def _prime_cached_helper(monkeypatch):
    entered = []

    async def _cached(**kwargs):
        entered.append(kwargs)
        return ["channel-message-hit"]

    monkeypatch.setattr(route, "_search_messages_cached", _cached)
    return entered


def _wire(monkeypatch, servers):
    monkeypatch.setattr(api, "get_servers", lambda: servers)
    monkeypatch.setattr(api, "get_messaging", lambda: _Messaging())


@pytest.mark.asyncio
async def test_alias_read_denied_member_cannot_search(monkeypatch):
    entered = _prime_cached_helper(monkeypatch)
    servers = _Servers(messages_read=False)
    _wire(monkeypatch, servers)

    with pytest.raises(HTTPException) as exc:
        await route.search_messages(str(CONV_ID), "secret", 25, _viewer())

    assert exc.value.status_code in (403, 404)
    # The grant was checked on the REAL channel id, not the URL alias.
    assert (
        VIEWER_ID,
        SERVER_ID,
        "messages.read",
        CHANNEL_ID,
    ) in servers.permission_calls
    assert entered == []


@pytest.mark.asyncio
async def test_alias_read_granted_member_can_search(monkeypatch):
    """Control: the alias gate must not block a legitimate reader."""
    entered = _prime_cached_helper(monkeypatch)
    servers = _Servers(messages_read=True)
    _wire(monkeypatch, servers)

    result = await route.search_messages(str(CONV_ID), "harmless", 25, _viewer())

    assert result == ["channel-message-hit"]
    assert (
        VIEWER_ID,
        SERVER_ID,
        "messages.read",
        CHANNEL_ID,
    ) in servers.permission_calls
    assert entered and entered[0]["conv_id"] == CONV_ID


@pytest.mark.asyncio
async def test_alias_gate_fails_closed(monkeypatch):
    entered = _prime_cached_helper(monkeypatch)
    servers = _Servers(messages_read=True, break_permission=True)
    _wire(monkeypatch, servers)

    with pytest.raises(HTTPException) as exc:
        await route.search_messages(str(CONV_ID), "secret", 25, _viewer())

    assert exc.value.status_code in (403, 404)
    assert entered == []
