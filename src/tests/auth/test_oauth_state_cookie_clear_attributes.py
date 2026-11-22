"""OAuth state-binding cookie deletion must mirror its issuance attributes.

The state cookie (``plexichat_oauth_state``) is issued as an auth/CSRF binding
cookie with ``HttpOnly``, ``Secure``, ``SameSite=Lax`` and an OAuth-scoped
``Path``.  On a failed callback and on a successful one the server emits a
``Set-Cookie`` that clears it.  Those deletion headers were built with only a
``Path``: the emitted cookie was missing ``HttpOnly`` and ``Secure``.

A clear that omits ``Secure`` can fail to remove a Secure-only cookie in
browsers that keep secure and non-secure variants of the same name in distinct
cookie stores, and emitting an auth-cookie ``Set-Cookie`` without the
credential protections is itself a fail-open hygiene bug.  The clear must
carry the exact same protection attributes as the issuance.
"""

import pytest
from fastapi import Response

pytestmark = pytest.mark.auth

_STATE_COOKIE = "plexichat_oauth_state"


def _assert_hardened_clear(header: str, expected_path: str) -> None:
    """Every state-cookie clear must carry the issuance protections."""
    h = header.lower()
    assert f"{_STATE_COOKIE}=" in h, "header must target the state cookie"
    assert "max-age=0" in h, "clear must expire the cookie immediately"
    assert f"path={expected_path.lower()}" in h, "clear must reuse the scoped Path"
    assert "samesite=lax" in h, "clear must carry SameSite=Lax"
    assert "httponly" in h, "state-cookie clear must carry HttpOnly"
    assert "secure" in h, "state-cookie clear must carry Secure in prod config"


def _expect_path(oauth_mod) -> str:
    return oauth_mod._oauth_cookie_params(60)["path"]


def test_failed_callback_clear_headers_hardened(monkeypatch):
    """The clear attached to rejected callbacks must be fully hardened."""
    from src.api.routes.auth import oauth as oauth_mod

    # Simulate the production configuration where Secure is enabled.  The
    # test harness relaxes it for plain-HTTP testserver, which would mask the
    # missing Secure attribute.
    monkeypatch.setattr(oauth_mod, "session_cookie_secure", lambda: True)

    headers = oauth_mod._oauth_state_cookie_clear_headers()
    _assert_hardened_clear(headers["set-cookie"], _expect_path(oauth_mod))


def test_success_callback_clear_helper_hardened(monkeypatch):
    """The clear performed on a successful callback must be fully hardened."""
    from src.api.routes.auth import oauth as oauth_mod

    monkeypatch.setattr(oauth_mod, "session_cookie_secure", lambda: True)

    resp = Response()
    oauth_mod._clear_oauth_state_cookie(resp)
    clears = [
        v
        for v in resp.headers.getlist("set-cookie")
        if v.lower().startswith(f"{_STATE_COOKIE}=")
    ]
    assert clears, "successful callback must emit a state-cookie clear"
    _assert_hardened_clear(clears[0], _expect_path(oauth_mod))
