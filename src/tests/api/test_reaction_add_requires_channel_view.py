"""Regression: adding a reaction must require the ability to VIEW the channel.

The reaction write path (``PUT .../reactions/{emoji}``) validated the message /
channel binding and the ``messages.add_reactions`` permission, but never
checked ``messages.read``.  The core manager deliberately treats those as
separate bits: a channel override can deny view access while leaving the
``@everyone`` ``messages.add_reactions`` grant intact (the reaction fan-out code
already hides such "invisible" members from events).  A server member who could
not see a private channel could therefore still react to its messages by
pairing a known message id with the channel path -- reacting to a message they
cannot see and injecting a reaction pill visible to everyone who *can* see it.

These tests pin the boundary: an authorized viewer may react, a member denied
``messages.read`` may not, and the live permission check runs before the write.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core.auth.models import AccountType, TokenInfo

pytestmark = [pytest.mark.unit, pytest.mark.security]

CHANNEL_ID = "42"
MESSAGE_ID = "99"
CONVERSATION_ID = 77
SERVER_ID = 7
EMOJI = "%3Asecret%3A"  # ":secret:"
USER_ID = 42


def _token() -> TokenInfo:
    return TokenInfo(
        valid=True,
        token_type="user",
        account_id=USER_ID,
        user_id=USER_ID,
        session_id=1,
        permissions={},
        rate_limit_tier="user",
        expires_at=None,
        username="hidden_member",
        account_type=AccountType.USER,
    )


class _FakeReactions:
    def __init__(self):
        self.add_calls = []

    def resolve_message_channel(self, message_id):
        return {"channel_id": int(CHANNEL_ID), "is_server_channel": True}

    def get_conversation_id_from_message(self, message_id):
        return CONVERSATION_ID

    def add_reaction(self, user_id, message_id, emoji):
        self.add_calls.append((user_id, message_id, emoji))
        return object()

    def get_participant_ids(self, conversation_id):
        return []


class _Channel:
    server_id = SERVER_ID


class _FakeServers:
    def __init__(self, *, can_view: bool):
        self.can_view = can_view
        self.permission_calls = []

    def get_channel_by_conversation(self, conversation_id):
        return {"id": int(CHANNEL_ID), "server_id": SERVER_ID}

    def has_permission(self, user_id, server_id, permission, channel_id=None):
        self.permission_calls.append((user_id, server_id, permission, channel_id))
        if permission == "messages.read":
            return self.can_view
        # The @everyone grant still allows adding reactions; the point is that
        # view access is a separate, independently-denied bit.
        return True

    def get_channel(self, channel_id, user_id):
        return _Channel()

    def is_timed_out(self, user_id, server_id):
        return False


@pytest.fixture
def env(monkeypatch):
    from types import SimpleNamespace

    from src.api.middleware.authentication import get_current_user
    from src.api.routes import reactions as routes

    # Rate limiting is orthogonal to authorization; allow every call.
    async def _allow(**kwargs):
        return SimpleNamespace(allowed=True, retry_after=0)

    monkeypatch.setattr(routes.ratelimit, "acheck_rate_limit", _allow)
    monkeypatch.setattr(routes.asyncio, "create_task", lambda coro: coro.close())

    fake_reactions = _FakeReactions()
    monkeypatch.setattr(routes.api, "get_reactions", lambda: fake_reactions)

    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[get_current_user] = _token

    def _install(can_view):
        fake_servers = _FakeServers(can_view=can_view)
        monkeypatch.setattr(routes.api, "get_servers", lambda: fake_servers)
        return fake_servers

    return TestClient(app), fake_reactions, _install


def _put(client):
    return client.put(f"/channels/{CHANNEL_ID}/messages/{MESSAGE_ID}/reactions/{EMOJI}")


def test_hidden_member_cannot_react_to_invisible_channel_message(env):
    client, fake_reactions, _install = env
    servers = _install(can_view=False)

    resp = _put(client)

    assert resp.status_code == 403, resp.text
    assert fake_reactions.add_calls == []
    # The live view check must have run before the write was rejected.
    assert (USER_ID, SERVER_ID, "messages.read", int(CHANNEL_ID)) in (
        servers.permission_calls
    )


def test_authorized_member_can_still_react(env):
    client, fake_reactions, _install = env
    servers = _install(can_view=True)

    resp = _put(client)

    assert resp.status_code == 200, resp.text
    assert fake_reactions.add_calls == [(USER_ID, int(MESSAGE_ID), ":secret:")]
    assert (USER_ID, SERVER_ID, "messages.read", int(CHANNEL_ID)) in (
        servers.permission_calls
    )
