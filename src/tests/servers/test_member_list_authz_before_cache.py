"""The cached member-list GET must re-check membership on every request.

``get_server_members`` memoizes each viewer's roster page in the
``server_members_api`` namespace. Its only membership check lives inside
``servers_mod.get_members`` -- which is called inside the cached body -- so a
warm ``@cached`` hit returns before that check runs. A member removed by a path
without an API-layer eviction (automod, an admin action, or a bot calling the
core manager) kept reading a still-warm 200 roster listing the server for the
whole TTL.

A live membership gate now runs as a FastAPI dependency, which resolves before
the ``@cached`` wrapper reads the cache. These tests pin that a warm cache entry
must never replace the authorization decision, and that the gate fails closed.
"""

from __future__ import annotations

from inspect import signature

import pytest
from fastapi import FastAPI
from fastapi.params import Depends as DependsParam
from fastapi.testclient import TestClient

import src.api as api
from src.api.middleware.authentication import get_current_user
from src.api.routes.servers import members as members_routes
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
        self.raise_on_check = False

    def get_member(self, sid, uid):
        self.member_checks += 1
        if self.raise_on_check:
            raise RuntimeError("membership backend down")
        return object() if self.is_member else None

    def get_members(self, viewer_id, sid, limit, after):
        self.list_calls += 1
        # Rows are returned even after revocation: only a live pre-cache gate
        # can deny the second (still-warm) read.
        return [
            {
                "id": 100 + viewer_id,
                "user_id": viewer_id,
                "nickname": "before",
                "joined_at": 1,
                "roles": [],
                "timeout_until": None,
                "timeout_reason": None,
            }
        ]


@pytest.fixture
def members_client(monkeypatch):
    fake = _FakeServers()
    monkeypatch.setattr(api, "get_servers", lambda: fake)
    monkeypatch.setattr(api, "get_auth", lambda: None)
    monkeypatch.setattr(api, "get_presence", lambda: None)
    # Force the in-process cache tier so the test is independent of Valkey.
    import src.core.database.cache.decorators as decorators

    monkeypatch.setattr(decorators, "get_client", lambda: None)
    monkeypatch.setattr(decorators, "is_available", lambda: False)

    mem_cache_clear_pattern("*")
    app = FastAPI()
    app.include_router(members_routes.router)
    app.dependency_overrides[get_current_user] = lambda: _TokenInfo(7)
    with TestClient(app) as client:
        try:
            yield client, fake
        finally:
            mem_cache_clear_pattern("*")


def test_member_list_gate_is_a_live_dependency():
    """FastAPI must resolve the gate before the cached handler is entered."""
    sig = signature(members_routes.get_server_members)
    gates = [
        p.default
        for p in sig.parameters.values()
        if isinstance(p.default, DependsParam)
        and p.default.dependency is members_routes._require_member_list_access
    ]
    assert gates, "no live membership dependency on the cached member-list handler"


def test_revoked_member_denied_even_with_warm_cache(members_client):
    client, fake = members_client

    first = client.get("/123/members")
    assert first.status_code == 200, first.text
    assert first.json().get("items"), "non-vacuous priming failed"
    assert fake.member_checks >= 1
    assert mem_cache_matching_keys("server_members_api:*"), "cache not warmed"
    assert fake.list_calls == 1

    # Revoke the membership WITHOUT invalidating the API cache. The first
    # response is still cached, so only a live pre-cache gate can deny this.
    fake.is_member = False
    checks_before = fake.member_checks
    second = client.get("/123/members")
    assert second.status_code == 403, second.text
    assert fake.member_checks > checks_before, (
        "membership was not re-checked on the cached request"
    )
    assert fake.list_calls == 1, "cached body was executed after revocation"


def test_gate_rejects_invalid_server_id(members_client):
    client, _fake = members_client
    response = client.get("/not-a-snowflake/members")
    assert response.status_code == 400


def test_gate_fails_closed_on_backend_error(members_client):
    client, fake = members_client
    fake.raise_on_check = True
    response = client.get("/123/members")
    assert response.status_code == 500


def test_gate_fails_closed_when_servers_module_missing(monkeypatch):
    """A missing servers module must deny, never skip the gate."""
    from fastapi import HTTPException

    monkeypatch.setattr(api, "get_servers", lambda: None)
    with pytest.raises(HTTPException) as excinfo:
        members_routes._require_member_list_access("123", _TokenInfo(7))
    assert excinfo.value.status_code == 503
