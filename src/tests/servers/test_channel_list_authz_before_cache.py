"""The cached channel-list GET must re-check membership on every request.

``get_server_channels`` memoizes its per-viewer response in the
``server_channels_api`` namespace. Its cold path enforces membership via
``servers_mod.get_channels`` (which itself gates ``channels.view`` per channel),
but a warm ``@cached`` hit returns from the wrapper before that body runs. A
member removed by a path without an API-layer eviction (automod, an admin path
or a bot calling the core manager) kept reading a still-warm 200 listing the
channels they could see before the revocation, for the whole TTL.

The live membership gate now runs as a FastAPI dependency, which resolves
before the ``@cached`` wrapper reads the cache. These tests pin that: a warm
cache entry must never replace the authorization decision.
"""

from __future__ import annotations

from inspect import signature
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.params import Depends as DependsParam
from fastapi.testclient import TestClient

import src.api as api
from src.api.middleware.authentication import get_current_user
from src.api.routes.servers import channels as channels_routes
from src.core.database.cache.base import (
    mem_cache_clear_pattern,
    mem_cache_matching_keys,
)

pytestmark = [pytest.mark.unit, pytest.mark.security]


class _TokenInfo:
    """Minimal stand-in with the real class name used by cache key builders."""

    def __init__(self, user_id: int) -> None:
        self.user_id = user_id
        self.token_type = "user"
        self.account_id = user_id


class _FakeServers:
    def __init__(self) -> None:
        self.is_member = True
        self.member_checks = 0
        self.list_calls = 0

    def server_exists(self, sid) -> bool:
        return True

    def get_member(self, sid, uid):
        self.member_checks += 1
        return object() if self.is_member else None

    def get_channels(self, uid, sid):
        self.list_calls += 1
        # Rows are returned even after revocation: only a live pre-cache gate
        # can deny the second (still-warm) read.
        return [
            SimpleNamespace(
                id=11,
                server_id=sid,
                name="general",
                channel_type="text",
                created_at=1,
            )
        ]


@pytest.fixture
def channels_client(monkeypatch):
    fake = _FakeServers()
    monkeypatch.setattr(api, "get_servers", lambda: fake)
    # Force the in-process cache tier so the test is independent of Valkey.
    import src.core.database.cache.decorators as decorators

    monkeypatch.setattr(decorators, "get_client", lambda: None)
    monkeypatch.setattr(decorators, "is_available", lambda: False)

    mem_cache_clear_pattern("*")
    app = FastAPI()
    app.include_router(channels_routes.router)
    app.dependency_overrides[get_current_user] = lambda: _TokenInfo(7)
    with TestClient(app) as client:
        try:
            yield client, fake
        finally:
            mem_cache_clear_pattern("*")


def test_channel_list_gate_is_a_live_dependency():
    """FastAPI must resolve the gate before the cached handler is entered."""
    sig = signature(channels_routes.get_server_channels)
    gates = [
        p.default
        for p in sig.parameters.values()
        if isinstance(p.default, DependsParam)
        and p.default.dependency is channels_routes._require_channel_list_access
    ]
    assert gates, "no live membership dependency on the cached channel-list handler"


def test_revoked_member_denied_even_with_warm_cache(channels_client):
    client, fake = channels_client

    first = client.get("/123/channels")
    assert first.status_code == 200, first.text
    assert first.json(), "non-vacuous priming failed"
    assert fake.member_checks >= 1
    assert mem_cache_matching_keys("server_channels_api:*"), "cache not warmed"
    assert fake.list_calls == 1

    # Revoke the membership WITHOUT invalidating the API cache. The first
    # response is still cached, so only a live pre-cache gate can deny this.
    fake.is_member = False
    checks_before = fake.member_checks
    second = client.get("/123/channels")
    assert second.status_code == 403, second.text
    assert fake.member_checks > checks_before, (
        "membership was not re-checked on the cached request"
    )
    assert fake.list_calls == 1, "cached body was executed after revocation"


def test_gate_rejects_invalid_server_id(channels_client):
    client, _fake = channels_client
    response = client.get("/not-a-snowflake/channels")
    assert response.status_code == 400
