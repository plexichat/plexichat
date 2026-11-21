"""Channel message search must require per-channel ``messages.read`` access.

``GET /channels/{channel_id}/messages/search`` resolved the channel with
``servers.get_channel`` only to obtain a conversation id, then authorized the
read with ``messaging.is_participant``.  That participant check proves DM
participation *or mere membership of the parent server* -- it never consults
the channel's ``messages.read`` grant.  The normal channel message listing
path (``get_channel_messages``) does require that grant, so a server member
denied read access on a channel could still search (and read) its messages via
this endpoint, including a still-warm result page from the search cache.

These tests pin the boundary: the per-channel read grant must be checked live
before the cached result body is served, and a caller who is a server member
but has no ``messages.read`` on the channel is denied.

Run with: pytest src/tests/api/test_channel_message_search_requires_read_access.py -q -n 0
"""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import src.api as api
import src.api.routes.messages.search as route
from src.core.auth.models import AccountType, TokenInfo

pytestmark = [pytest.mark.unit, pytest.mark.security]

CHANNEL_ID = 500
SERVER_ID = 7
CONVERSATION_ID = 9000
VIEWER_ID = 42


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
        username="member_without_read",
        account_type=AccountType.USER,
    )


class _Messaging:
    """Models ParticipantService: server membership alone grants participation."""

    def __init__(self):
        self.checked = []

    def is_participant(self, conversation_id, user_id):
        self.checked.append((conversation_id, user_id))
        return True


class _Servers:
    """Servers stub: channel exists, is viewable, but read may be denied."""

    def __init__(self, *, messages_read: bool):
        self.messages_read = messages_read
        self.permission_calls = []

    def channel_exists(self, channel_id):
        return channel_id == CHANNEL_ID

    def get_channel(self, channel_id, user_id):
        # channels.view is granted, so the channel resolves to its conversation.
        return SimpleNamespace(
            id=channel_id,
            server_id=SERVER_ID,
            conversation_id=CONVERSATION_ID,
        )

    def has_permission(self, user_id, server_id, permission, channel_id=None):
        self.permission_calls.append((user_id, server_id, permission, channel_id))
        if permission == "messages.read":
            return self.messages_read
        return True


def _prime_cached_helper(monkeypatch):
    entered = []

    async def _cached(**kwargs):
        entered.append(kwargs)
        return ["channel-message-hit"]

    monkeypatch.setattr(route, "_search_messages_cached", _cached)
    return entered


@pytest.mark.asyncio
async def test_member_denied_read_cannot_search_channel(monkeypatch):
    """A server member without messages.read must never get the result body."""
    entered = _prime_cached_helper(monkeypatch)
    servers = _Servers(messages_read=False)
    monkeypatch.setattr(api, "get_servers", lambda: servers)
    monkeypatch.setattr(api, "get_messaging", lambda: _Messaging())

    with pytest.raises(HTTPException) as exc:
        await route.search_messages(str(CHANNEL_ID), "secret", 25, _viewer())

    assert exc.value.status_code in (403, 404)
    # The live per-channel read check ran...
    assert (
        VIEWER_ID,
        SERVER_ID,
        "messages.read",
        CHANNEL_ID,
    ) in servers.permission_calls
    # ...and no cached/paged result body was served.
    assert entered == []


@pytest.mark.asyncio
async def test_member_with_read_can_search_channel(monkeypatch):
    """The gate must not be a blanket denial: a reader still gets results."""
    entered = _prime_cached_helper(monkeypatch)
    servers = _Servers(messages_read=True)
    monkeypatch.setattr(api, "get_servers", lambda: servers)
    messaging = _Messaging()
    monkeypatch.setattr(api, "get_messaging", lambda: messaging)

    result = await route.search_messages(str(CHANNEL_ID), "secret", 25, _viewer())

    assert result == ["channel-message-hit"]
    assert (
        VIEWER_ID,
        SERVER_ID,
        "messages.read",
        CHANNEL_ID,
    ) in servers.permission_calls
    assert messaging.checked == [(CONVERSATION_ID, VIEWER_ID)]
    assert entered and entered[0]["conv_id"] == CONVERSATION_ID


@pytest.mark.asyncio
async def test_missing_channel_permission_source_fails_closed(monkeypatch):
    """If the backing channel's permission cannot be resolved, deny."""
    entered = _prime_cached_helper(monkeypatch)

    class _NoPermissionSource(_Servers):
        def has_permission(self, *args, **kwargs):
            self.permission_calls.append(args)
            raise RuntimeError("permission store unavailable")

    servers = _NoPermissionSource(messages_read=True)
    monkeypatch.setattr(api, "get_servers", lambda: servers)
    monkeypatch.setattr(api, "get_messaging", lambda: _Messaging())

    with pytest.raises(HTTPException) as exc:
        await route.search_messages(str(CHANNEL_ID), "secret", 25, _viewer())

    assert exc.value.status_code in (403, 404)
    assert entered == []
