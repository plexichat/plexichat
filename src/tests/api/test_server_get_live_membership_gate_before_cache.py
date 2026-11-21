"""GET /servers/{id} must re-check membership before a warm cache hit.

``get_server`` carried ``@cached`` directly on the route handler with the
membership gate (``servers_mod.get_server(sid, viewer)``) *inside* the cached
body. On a warm hit the decorator returns before that body runs, so a member who
was kicked/banned (by a path that does not evict this API namespace -- there is
no invalidator for the ``get_server`` key at all) kept receiving the full server
response from a still-warm entry for the whole 60s TTL.

The live gate now runs as a FastAPI dependency, which FastAPI resolves before
entering the endpoint (and therefore before ``@cached`` reads the cache). These
tests pin that a cached body can never substitute for the authorization
decision. Run with::

    pytest src/tests/api/test_server_get_live_membership_gate_before_cache.py -q
"""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.api as api
from src.api.middleware.authentication import get_current_user
from src.api.routes.servers import server_crud
from src.core.database.cache.base import (
    mem_cache_clear_pattern,
    mem_cache_matching_keys,
)

pytestmark = [pytest.mark.api, pytest.mark.security]

SID = 4242
UID = 77


class TokenInfo:
    """Stand-in named like the real credential so the default key builder keys
    on ``user_id`` instead of the instance repr (which contains an address)."""

    def __init__(self, user_id):
        self.user_id = user_id
        self.token_type = "user"
        self.account_id = user_id


def _fake_server():
    return SimpleNamespace(
        id=SID,
        name="Warm Server",
        description="private description",
        icon_url=None,
        owner_id=99,
        member_count=3,
        default_channel_id=None,
        verification_level=0,
        default_message_notifications=0,
        max_reactions_per_message=20,
        created_at=1,
    )


class _FakeServers:
    """Fake manager: both the gate probe and the fetch consult the same store."""

    def __init__(self):
        self.members = {UID}
        self.get_member_calls = 0
        self.get_server_calls = 0
        self.raises = False

    def get_member(self, sid, uid):
        self.get_member_calls += 1
        if self.raises:
            raise RuntimeError("member store unavailable")
        return object() if (sid == SID and uid in self.members) else None

    def get_server(self, sid, uid):
        self.get_server_calls += 1
        return _fake_server() if (sid == SID and uid in self.members) else None


@pytest.fixture
def server_get_client(monkeypatch):
    fake = _FakeServers()
    monkeypatch.setattr(api, "get_servers", lambda: fake)
    # Force the process-local tier so the test is independent of Valkey.
    import src.core.database.cache.decorators as decorators
    import src.core.database.cache.operations as operations

    monkeypatch.setattr(decorators, "get_client", lambda: None)
    monkeypatch.setattr(decorators, "is_available", lambda: False)
    monkeypatch.setattr(operations, "get_client", lambda: None)

    mem_cache_clear_pattern("*get_server*")
    app = FastAPI()
    app.include_router(server_crud.router)
    app.dependency_overrides[get_current_user] = lambda: TokenInfo(UID)
    with TestClient(app) as client:
        try:
            yield client, fake
        finally:
            mem_cache_clear_pattern("*get_server*")


def test_revoked_member_denied_on_warm_server_get_cache(server_get_client):
    """A kicked member cannot read a still-warm server body; the live gate denies."""
    client, fake = server_get_client

    first = client.get(f"/{SID}")
    assert first.status_code == 200, first.text
    assert first.json()["name"] == "Warm Server"
    assert fake.get_server_calls == 1
    assert mem_cache_matching_keys("*get_server*"), "server-get cache was not warmed"

    # Membership is revoked behind the cache's back. No eviction helper is
    # invoked -- an authorization boundary must not depend on one.
    fake.members.clear()

    second = client.get(f"/{SID}")
    assert second.status_code in (403, 404), second.text
    # The authoritative gate ran on this (would-be cache hit) request ...
    assert fake.get_member_calls >= 2
    # ... and the cached data body was never served (the fetch count is pinned
    # in test_gate_reruns_while_backend_fetch_stays_cached).


def test_gate_reruns_while_backend_fetch_stays_cached(server_get_client):
    """The membership probe re-runs on every read; the expensive fetch is memoized."""
    client, fake = server_get_client

    assert client.get(f"/{SID}").status_code == 200
    assert fake.get_member_calls == 1
    assert fake.get_server_calls == 1

    # Still a member: a warm hit must not re-enter the backend fetch.
    again = client.get(f"/{SID}")
    assert again.status_code == 200, again.text
    assert again.json()["name"] == "Warm Server"
    assert fake.get_member_calls == 2
    assert fake.get_server_calls == 1

    # Revoked: gate re-runs and denies, fetch still never re-entered.
    fake.members.clear()
    denied = client.get(f"/{SID}")
    assert denied.status_code in (403, 404), denied.text
    assert fake.get_member_calls == 3
    assert fake.get_server_calls == 1


def test_failed_membership_probe_fails_closed_on_warm_cache(server_get_client):
    """If the membership store errors, the warm response must not be served."""
    client, fake = server_get_client

    assert client.get(f"/{SID}").status_code == 200
    assert mem_cache_matching_keys("*get_server*")

    fake.raises = True
    denied = client.get(f"/{SID}")
    assert denied.status_code in (403, 404, 500), denied.text
    assert fake.get_server_calls == 1
