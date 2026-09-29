"""API-level routing tests for the reaction routes.

These dispatch REAL requests through the reactions router and assert which
endpoint implementation fires. They exist because Starlette matches routes in
registration order and the ``{emoji:path}`` convertor is greedy: if the bare
``DELETE .../reactions/{emoji:path}`` route is registered before the moderator
wipe ``DELETE .../reactions/{emoji:path}/all``, the wipe becomes unreachable
(the trailing "/all" is absorbed into ``emoji``).
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core.auth.models import AccountType, TokenInfo


class _RateLimitResult:
    allowed = True
    retry_after = 0


class _RecorderReactions:
    """Fake reactions manager recording which wipe/remove was invoked."""

    def __init__(self):
        self.calls = []

    def resolve_message_channel(self, message_id):
        return {"channel_id": 42}

    def remove_all_reactions_for_emoji(self, user_id, message_id, emoji):
        self.calls.append(("remove_all_for_emoji", user_id, message_id, emoji))
        return 3

    def remove_reaction(self, user_id, message_id, emoji):
        self.calls.append(("remove_reaction", user_id, message_id, emoji))


def _token_info() -> TokenInfo:
    return TokenInfo(
        valid=True,
        token_type="user",
        account_id=7,
        user_id=7,
        session_id=1,
        permissions={},
        rate_limit_tier="user",
        expires_at=None,
        username="tester",
        account_type=AccountType.USER,
    )


@pytest.fixture
def dispatch_env(monkeypatch):
    """Build a minimal app hosting ONLY the reactions router, with auth and
    rate limiting stubbed so the request reaches the endpoint under test."""
    from src.api.middleware.authentication import get_current_user
    from src.core import ratelimit
    from src.api.routes import reactions as reactions_routes

    recorder = _RecorderReactions()
    monkeypatch.setattr(reactions_routes.api, "get_reactions", lambda: recorder)

    async def _allow_rate_limit(**kwargs):
        return _RateLimitResult()

    # The DELETE routes call the ASYNC rate-limit entry point; stub both so the
    # test no longer depends on a ratelimit manager leaked from an earlier test
    # (which raised once api fixtures started resetting the global manager).
    monkeypatch.setattr(
        ratelimit,
        "check_rate_limit",
        lambda **kwargs: _RateLimitResult(),
    )
    monkeypatch.setattr(ratelimit, "acheck_rate_limit", _allow_rate_limit)

    app = FastAPI()
    app.include_router(reactions_routes.router)
    app.dependency_overrides[get_current_user] = _token_info

    client = TestClient(app)
    return client, recorder


def test_moderator_emoji_wipe_route_fires(dispatch_env):
    """DELETE .../reactions/{emoji}/all must hit the MODERATOR wipe endpoint,
    not the single-user remove (route-order shadowing regression)."""
    client, recorder = dispatch_env

    resp = client.delete("/channels/42/messages/99/reactions/:sob:/all")

    assert resp.status_code == 200, resp.text
    assert resp.json()["message"] == "Removed 3 reactions"
    assert ("remove_all_for_emoji", 7, 99, ":sob:") in recorder.calls
    assert not any(c[0] == "remove_reaction" for c in recorder.calls)


def test_single_user_remove_route_still_fires(dispatch_env):
    """The plain emoji DELETE must still reach the single-user remove."""
    client, recorder = dispatch_env

    resp = client.delete("/channels/42/messages/99/reactions/:sob:")

    assert resp.status_code == 200, resp.text
    assert ("remove_reaction", 7, 99, ":sob:") in recorder.calls
    assert not any(c[0] == "remove_all_for_emoji" for c in recorder.calls)


def test_wipe_route_registered_before_bare_emoji_route():
    """Guard on registration order itself: the greedy {emoji:path} DELETE must
    come AFTER the /all wipe route, otherwise the wipe is unreachable."""
    from src.api.routes.reactions import router

    delete_paths = [
        (route.path, getattr(route.endpoint, "__name__", ""))
        for route in router.routes
        if "DELETE" in getattr(route, "methods", set())
        and "reactions" in route.path
        and "{emoji:path}" in route.path
    ]
    paths_only = [p for p, _ in delete_paths]
    wipe_idx = next(
        i for i, p in enumerate(paths_only) if p.endswith("{emoji:path}/all")
    )
    bare_idx = paths_only.index(
        "/channels/{channel_id}/messages/{message_id}/reactions/{emoji:path}"
    )
    assert wipe_idx < bare_idx
