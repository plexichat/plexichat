"""Regression: raw-token verification must honour IP/UA binding.

``get_token_info`` is used by the signed-media route to identify the caller
without going through ``get_current_user``. It used to re-verify the token with
``verify_token(token, None, None)`` on a per-request cache miss, which skips
IP/User-Agent binding entirely. A credential the auth middleware rejected for a
binding mismatch could therefore be replayed against a signed media URL to
impersonate its owner. The helper now forwards the binding context and fails
closed when it does not match.
"""

import pytest

from src.api.middleware import authentication as auth_mw
from src.api.middleware.request_context import (
    RequestContext,
    bind_current_context,
    reset_current_context,
)
from src.core.auth.exceptions import TokenInvalidError
from src.core.auth.models import AccountType, TokenInfo


VALID_IP = "203.0.113.7"
VALID_UA = "LegitClient/1.0"


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


class _BindingAuth:
    """Only accepts the token when the caller's binding context matches."""

    def __init__(self):
        self.calls = []

    def verify_token(self, token, ip_address=None, user_agent=None):
        self.calls.append((token, ip_address, user_agent))
        if ip_address == VALID_IP and user_agent == VALID_UA:
            return _token_info()
        raise TokenInvalidError("IP/User-Agent binding mismatch")


@pytest.fixture
def bound_context():
    token = bind_current_context(RequestContext())
    try:
        yield
    finally:
        reset_current_context(token)


@pytest.mark.asyncio
async def test_get_token_info_forwards_binding_context(monkeypatch, bound_context):
    auth = _BindingAuth()
    monkeypatch.setattr("src.api.get_auth", lambda: auth)

    info = await auth_mw.get_token_info("tok", ip_address=VALID_IP, user_agent=VALID_UA)

    assert info is not None
    assert info.user_id == 42
    assert auth.calls == [("tok", VALID_IP, VALID_UA)]


@pytest.mark.asyncio
async def test_get_token_info_rejects_binding_mismatch(monkeypatch, bound_context):
    """A token rejected by the middleware stays rejected on the cache-miss path."""
    auth = _BindingAuth()
    monkeypatch.setattr("src.api.get_auth", lambda: auth)

    info = await auth_mw.get_token_info(
        "tok", ip_address="198.51.100.23", user_agent="Attacker/1.0"
    )

    assert info is None
    assert auth.calls == [("tok", "198.51.100.23", "Attacker/1.0")]


@pytest.mark.asyncio
async def test_get_token_info_reuses_middleware_result(monkeypatch, bound_context):
    """A result already verified by the middleware is still reused verbatim."""
    auth = _BindingAuth()
    monkeypatch.setattr("src.api.get_auth", lambda: auth)

    sentinel = _token_info()
    monkeypatch.setattr(auth_mw, "lookup_token_result", lambda token: (True, sentinel))

    info = await auth_mw.get_token_info(
        "tok", ip_address="198.51.100.23", user_agent="Attacker/1.0"
    )

    assert info is sentinel
    assert auth.calls == []
