"""The privileged role listing must re-check authorization on every request.

``get_server_roles`` memoises its response for up to the cache TTL. Its cold
path enforced ``roles.manage`` *inside* the cached function, so a cache hit
never re-checked the permission. A caller whose grant was revoked by a path
that does not invalidate this API cache (e.g. an admin-side permission edit)
kept reading the privileged role set from a warm entry until the TTL expired.

The live gate now runs as a FastAPI dependency, which resolves before the
``@cached`` wrapper reads the cache. These tests pin that behaviour: a warm
cache entry must never replace the authorization decision.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.api as api
from src.api.middleware.authentication import get_current_user
from src.api.routes.servers import roles as roles_routes
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
        self.allow = True
        self.permission_checks = 0

    def require_permission(self, user_id, server_id, permission) -> None:
        self.permission_checks += 1
        if not self.allow:
            from src.core.servers.exceptions import PermissionDeniedError

            raise PermissionDeniedError("denied", permission)

    def get_roles(self, user_id, server_id):
        return []


@pytest.fixture
def roles_client(monkeypatch):
    fake = _FakeServers()
    monkeypatch.setattr(api, "get_servers", lambda: fake)
    # Force the in-process cache tier so the test is independent of Valkey.
    import src.core.database.cache.decorators as decorators

    monkeypatch.setattr(decorators, "get_client", lambda: None)
    monkeypatch.setattr(decorators, "is_available", lambda: False)

    mem_cache_clear_pattern("*")
    app = FastAPI()
    app.include_router(roles_routes.router)
    app.dependency_overrides[get_current_user] = lambda: _TokenInfo(7)
    with TestClient(app) as client:
        try:
            yield client, fake
        finally:
            mem_cache_clear_pattern("*")


def test_revoked_permission_denied_even_with_warm_cache(roles_client):
    client, fake = roles_client

    first = client.get("/123/roles")
    assert first.status_code == 200, first.text
    assert first.json() == []
    assert fake.permission_checks >= 1
    assert mem_cache_matching_keys("server_roles_api:*"), "roles cache was not warmed"

    # Revoke the permission WITHOUT invalidating the API cache. The first
    # response is still cached, so only a live pre-cache gate can deny this.
    fake.allow = False
    checks_before = fake.permission_checks
    second = client.get("/123/roles")
    assert second.status_code == 403, second.text
    assert fake.permission_checks > checks_before, (
        "authorization was not re-checked on the cached request"
    )


def test_gate_rejects_invalid_server_id(roles_client):
    client, _fake = roles_client
    response = client.get("/not-a-snowflake/roles")
    assert response.status_code == 400
