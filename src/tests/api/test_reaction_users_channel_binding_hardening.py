"""Regression: reactor-list reads must honor the URL channel scope.

``GET /channels/{channel_id}/messages/{message_id}/reactions/{emoji}`` used to
accept ``channel_id`` verbatim and then ignore it entirely. Every other
reaction route parses its ids through the shared snowflake gate and verifies
the message really belongs to the URL channel before reading or writing. The
reactor-list route skipped both checks, and unlike its sibling
``GET .../reactions`` it also skipped the cheap live ``can_access_message``
gate that must run before any (possibly warm) cached roster is consulted.

That let a caller pair a message id with an unrelated channel path -- defeating
channel-scoped authorization decisions made from the URL -- and left a revoked
viewer able to read a warm roster as long as the core manager's own gate had
not been re-run. These tests pin the boundary ordering: parse the channel id,
verify message/channel binding, re-check access, then read.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core.auth.models import AccountType, TokenInfo

pytestmark = [pytest.mark.unit]

CHANNEL_ID = "42"
OTHER_CHANNEL_ID = "43"
MESSAGE_ID = 99
EMOJI = "%F0%9F%91%8D"


def _token() -> TokenInfo:
    return TokenInfo(
        valid=True,
        token_type="user",
        account_id=42,
        user_id=42,
        session_id=1,
        permissions={},
        rate_limit_tier="user",
        expires_at=None,
        username="tester",
        account_type=AccountType.USER,
    )


class _FakeReactions:
    def __init__(self, *, real_channel_id=int(CHANNEL_ID), can_access=True):
        self.real_channel_id = real_channel_id
        self.can_access = can_access
        self.access_calls = []
        self.resolve_calls = []
        self.read_calls = []

    def can_access_message(self, user_id, message_id):
        self.access_calls.append((user_id, message_id))
        return self.can_access

    def resolve_message_channel(self, message_id):
        self.resolve_calls.append(message_id)
        return {"channel_id": self.real_channel_id}

    def get_reaction_users(self, **kwargs):
        self.read_calls.append(kwargs)
        return []


@pytest.fixture
def env(monkeypatch):
    from src.api.middleware.authentication import get_current_user
    from src.api.routes import reactions as reactions_routes

    fake = _FakeReactions()
    monkeypatch.setattr(
        reactions_routes.api, "get_reactions", lambda: fake, raising=False
    )

    app = FastAPI()
    app.include_router(reactions_routes.router)
    app.dependency_overrides[get_current_user] = _token
    return TestClient(app), fake


def _get(client, channel_id=CHANNEL_ID):
    return client.get(f"/channels/{channel_id}/messages/{MESSAGE_ID}/reactions/{EMOJI}")


def test_bad_channel_id_is_rejected_before_any_read(env):
    client, fake = env
    # Unicode decimal homoglyph for "4" + ASCII "2": int() would accept it,
    # the shared ASCII snowflake gate must not.
    resp = _get(client, channel_id="٤2")
    assert resp.status_code == 400, resp.text
    assert fake.access_calls == []
    assert fake.resolve_calls == []
    assert fake.read_calls == []


def test_message_from_another_channel_is_not_readable(env, monkeypatch):
    client, fake = env
    fake.real_channel_id = int(OTHER_CHANNEL_ID)
    resp = _get(client)
    assert resp.status_code == 404, resp.text
    assert fake.resolve_calls == [MESSAGE_ID]
    assert fake.read_calls == []


def test_revoked_viewer_cannot_read_a_warm_roster(env):
    client, fake = env
    fake.can_access = False
    resp = _get(client)
    assert resp.status_code == 404, resp.text
    assert fake.access_calls == [(42, MESSAGE_ID)]
    assert fake.resolve_calls == []
    assert fake.read_calls == []


def test_authorized_request_still_returns_the_roster(env):
    client, fake = env
    resp = _get(client)
    assert resp.status_code == 200, resp.text
    assert resp.json() == []
    assert fake.access_calls == [(42, MESSAGE_ID)]
    assert fake.resolve_calls == [MESSAGE_ID]
    assert len(fake.read_calls) == 1
