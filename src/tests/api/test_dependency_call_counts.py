"""Call-count regressions for the shared auth hot path.

133 endpoints sit in the 20-50 ms band and pay the same auth/session overhead.
These tests assert that a single representative request resolves the token,
user and client IP *once* even when several dependencies consume them, and that
the derived routing policy is rebuilt only when its inputs change. Counts are
deterministic, so they catch a regression without a wall-clock assertion.
"""

from types import SimpleNamespace

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from src.api.middleware.authentication import (
    AuthenticationMiddleware,
    get_current_user,
    get_optional_user,
    get_token_info,
)
from src.api.middleware.ip_blocking import IPBlockingMiddleware
from src.api.middleware.policy_cache import clear_static_policy_cache
from src.api.middleware.request_context import cached_client_ip, cached_is_self_test
from src.core.auth.models import AccountType, TokenInfo


def _token_info() -> TokenInfo:
    return TokenInfo(
        valid=True,
        token_type="user",
        account_id=42,
        user_id=42,
        session_id=7,
        permissions={},
        rate_limit_tier="standard",
        expires_at=None,
        username="tester",
        account_type=AccountType.USER,
    )


class _CountingAuth:
    """Auth double that records how often the expensive lookup actually runs."""

    def __init__(self) -> None:
        self.verify_calls = 0
        self.info = _token_info()

    def verify_token(self, token, *args, **kwargs):
        self.verify_calls += 1
        return self.info if token == "good" else None

    def is_api_access_token_required(self) -> bool:
        return False

    def is_ip_blocked(self, ip) -> bool:
        return False


@pytest.fixture
def auth_stack(monkeypatch):
    """A small app whose route consumes the user through three dependencies."""
    clear_static_policy_cache()
    cfg = SimpleNamespace(api_prefix="/api/v1")
    monkeypatch.setattr("src.api.config.get_api_config", lambda: cfg)

    fake_auth = _CountingAuth()
    monkeypatch.setattr("src.api.get_auth", lambda: fake_auth)
    monkeypatch.setattr("src.api.get_db", lambda: None)
    monkeypatch.setattr("src.api.is_self_test_request", lambda request: False)

    from src.utils.net import get_client_ip as real_get_client_ip

    ip_calls = []

    def counting_ip(obj):
        ip_calls.append(obj)
        return real_get_client_ip(obj)

    monkeypatch.setattr("src.utils.net.get_client_ip", counting_ip)

    app = FastAPI()

    @app.get("/api/v1/users/@me")
    async def me(
        current=Depends(get_current_user),
        optional=Depends(get_optional_user),
    ):
        via_token = await get_token_info("good")
        return {
            "id": current.user_id,
            "same_user": optional is current,
            "token_id": via_token.user_id if via_token else None,
        }

    # Last added is outermost: IP blocking resolves the IP first, then auth
    # must reuse it rather than parsing the proxy headers again.
    app.add_middleware(AuthenticationMiddleware)
    app.add_middleware(IPBlockingMiddleware)

    try:
        yield TestClient(app), fake_auth, ip_calls
    finally:
        clear_static_policy_cache()


def test_chained_dependencies_verify_token_and_ip_once(auth_stack):
    client, fake_auth, ip_calls = auth_stack

    response = client.get("/api/v1/users/@me", headers={"Authorization": "Bearer good"})

    assert response.status_code == 200
    assert response.json() == {
        "id": 42,
        "same_user": True,
        "token_id": 42,
    }
    # Middleware verification, mandatory dep, optional dep and the raw-token
    # helper all touch the same credential, but only one DB lookup happens.
    assert fake_auth.verify_calls == 1
    # IP blocking and authentication share one trusted-proxy resolution.
    assert len(ip_calls) == 1


def test_account_lock_still_enforced_after_caching(auth_stack):
    client, fake_auth, _ = auth_stack
    fake_auth.info.account_locked = True

    response = client.get("/api/v1/users/@me", headers={"Authorization": "Bearer good"})

    assert response.status_code == 403
    # The minimal app has no custom exception handler, so the detail may be
    # nested under FastAPI's default ``detail`` envelope.
    body = response.json()
    error = body.get("error") or body.get("detail", {}).get("error", {})
    assert error.get("message") == "Account suspended"


def test_request_helpers_resolve_each_fact_once():
    scope = {
        "type": "http",
        "path": "/api/v1/x",
        "headers": [],
        "client": ("1.2.3.4", 1234),
        "state": {},
    }
    ip_resolutions = []
    self_test_resolutions = []

    def ip_resolver(_scope):
        ip_resolutions.append(1)
        return "1.2.3.4"

    def self_test_resolver(_scope):
        self_test_resolutions.append(1)
        return False

    assert cached_client_ip(scope, resolver=ip_resolver) == "1.2.3.4"
    assert cached_client_ip(scope, resolver=lambda _: "9.9.9.9") == "1.2.3.4"
    assert len(ip_resolutions) == 1

    assert cached_is_self_test(scope, resolver=self_test_resolver) is False
    assert cached_is_self_test(scope, resolver=lambda _: True) is False
    assert len(self_test_resolutions) == 1


def test_static_policy_rebuilt_only_on_invalidation(monkeypatch):
    import src.api.middleware.policy_cache as policy_cache

    clear_static_policy_cache()
    cfg = SimpleNamespace(api_prefix="/api/v1")
    monkeypatch.setattr("src.api.config.get_api_config", lambda: cfg)

    real_loader = policy_cache._policy_snapshot._loader
    rebuilds = []

    def counting_loader():
        rebuilds.append(1)
        return real_loader()

    monkeypatch.setattr(policy_cache._policy_snapshot, "_loader", counting_loader)

    try:
        assert policy_cache.get_static_policy().api_prefix == "/api/v1"
        for _ in range(10):
            assert policy_cache.get_static_policy().api_prefix == "/api/v1"
        assert len(rebuilds) == 1

        policy_cache.clear_static_policy_cache()
        assert policy_cache.get_static_policy().api_prefix == "/api/v1"
        assert len(rebuilds) == 2
    finally:
        clear_static_policy_cache()
