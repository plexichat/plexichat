"""Boundary regressions for exact-username user discovery.

``GET /users/search`` used to hand an unbounded query parameter straight to
``auth.get_user_by_username`` (and to an error log line), and skipped its
anti-enumeration throttle whenever the limiter raised. Both are now bounded at
the HTTP boundary: oversized/control-character names are rejected before any
storage work, and a limiter that cannot be evaluated denies the request.
"""

import asyncio

import pytest
from fastapi import HTTPException

import src.api as api
from src.api.routes.users import discovery as discovery_route


class _User:
    def __init__(self, user_id=7):
        self.user_id = user_id


class _Auth:
    def __init__(self):
        self.lookups = []

    def get_user_by_username(self, username):
        self.lookups.append(username)
        return None


def _expect_status(status, coro):
    with pytest.raises(HTTPException) as exc:
        asyncio.run(coro)
    assert exc.value.status_code == status


@pytest.fixture
def auth_stub(monkeypatch):
    auth = _Auth()
    monkeypatch.setattr(api, "get_auth", lambda: auth)
    return auth


@pytest.fixture(autouse=True)
def _allow_requests(monkeypatch):
    """Keep the limiter out of the way unless a test overrides it."""
    import src.core.database.cache.rate_limit as rate_limit

    monkeypatch.setattr(rate_limit, "check_rate_limit", lambda *a, **k: (True, 10))


@pytest.mark.parametrize(
    "bad",
    [
        "a" * 33,  # longer than any valid username
        "a" * 5000,  # hostile query string
        "abc\x00def",  # NUL
        "abc\r\nX-Evil: 1",  # header/log forgery
        "abc\u2028def",  # unicode line separator
    ],
)
def test_oversized_or_control_username_never_reaches_storage(auth_stub, bad):
    handler = discovery_route.DiscoveryMixin().search_user_by_username
    _expect_status(400, handler(username=bad, current_user=_User()))
    assert auth_stub.lookups == []


def test_limiter_failure_fails_closed(auth_stub, monkeypatch):
    """A limiter that raises must deny, not reopen enumeration."""
    import src.core.database.cache.rate_limit as rate_limit

    def _broken(*args, **kwargs):
        raise RuntimeError("backend down")

    monkeypatch.setattr(rate_limit, "check_rate_limit", _broken)
    handler = discovery_route.DiscoveryMixin().search_user_by_username
    _expect_status(503, handler(username="somebody", current_user=_User()))
    assert auth_stub.lookups == []


def test_valid_username_still_resolves(auth_stub, monkeypatch):
    """A normal lookup keeps working (no accidental hardening regression)."""
    monkeypatch.setattr(
        discovery_route,
        "_user_to_public_response",
        lambda user: {"id": str(user.id)},
    )

    class _Found:
        id = 123

    auth_stub.get_user_by_username = lambda name: _Found()
    handler = discovery_route.DiscoveryMixin().search_user_by_username
    result = asyncio.run(handler(username="alice", current_user=_User()))
    assert result == {"id": "123"}
