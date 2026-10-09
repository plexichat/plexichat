"""Regression tests for M1: login/register must use proxy-aware client IP.

Covers:
1. X-Forwarded-For from a trusted proxy yields the real client IP
   (not the proxy/socket IP such as 127.0.0.1).
2. Direct connection without proxy headers still resolves to the socket IP.
3. Spoofed X-Forwarded-For from an UNTRUSTED source is NOT trusted
   (falls back to the socket IP).
Both the shared helper and the login/register route propagation are pinned.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

from fastapi import Request
from fastapi.responses import Response

import utils.config as config
from src.utils.net import get_client_ip


def _make_request(client_host, x_forwarded_for=None):
    headers = []
    if x_forwarded_for is not None:
        headers.append((b"x-forwarded-for", x_forwarded_for.encode()))
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/v1/auth/login",
        "headers": headers,
        "client": (client_host, 50000) if client_host else None,
    }
    return Request(scope)


def test_trusted_proxy_xff_yields_client_ip():
    config.set("api", {"trusted_proxies": ["127.0.0.1"]})
    req = _make_request("127.0.0.1", "203.0.113.7")
    assert get_client_ip(req) == "203.0.113.7"


def test_direct_connection_without_headers_uses_socket_ip():
    config.set("api", {"trusted_proxies": ["127.0.0.1"]})
    req = _make_request("192.0.2.10")
    assert get_client_ip(req) == "192.0.2.10"


def test_spoofed_xff_from_untrusted_source_ignored():
    config.set("api", {"trusted_proxies": ["10.0.0.0/8"]})
    # Direct peer is NOT a trusted proxy; header must be ignored.
    req = _make_request("203.0.113.9", "198.51.100.7")
    assert get_client_ip(req) == "203.0.113.9"


def _login_route_ip(request):
    from src.api.routes.auth import login as login_mod
    import src.api as api
    from src.api.schemas.auth import LoginRequest
    from src.core.auth.models import AuthStatus

    captured = {}

    class _Result:
        status = AuthStatus.SUCCESS
        token = "tok"
        user = None
        # Real ``AuthResult`` always carries a ``session`` (Optional[Session]);
        # the success route derives the cookie Max-Age from it.
        session = None
        challenge_token = None
        methods = None
        expires_in = None

    fake_auth = MagicMock()

    def _login(**kwargs):
        captured.update(kwargs)
        return _Result()

    fake_auth.login.side_effect = _login

    orig = api.get_auth
    api.get_auth = lambda: fake_auth
    try:
        asyncio.run(
            login_mod.login(
                request, Response(), LoginRequest(username="u", password="p")
            )
        )
    finally:
        api.get_auth = orig
    return captured.get("ip_address")


def test_login_route_uses_proxy_aware_ip():
    """Login must forward the proxy-resolved IP, not the socket IP."""
    config.set("api", {"trusted_proxies": ["127.0.0.1"]})
    assert _login_route_ip(_make_request("127.0.0.1", "203.0.113.7")) == "203.0.113.7"


def test_login_route_direct_connection_unchanged():
    config.set("api", {"trusted_proxies": ["127.0.0.1"]})
    assert _login_route_ip(_make_request("192.0.2.10")) == "192.0.2.10"


def test_login_route_ignores_spoofed_xff():
    config.set("api", {"trusted_proxies": ["10.0.0.0/8"]})
    assert (
        _login_route_ip(_make_request("203.0.113.9", "198.51.100.7")) == "203.0.113.9"
    )


def _register_route_ips(request, username="m1reg"):
    from src.api.routes.auth import register as reg_mod
    import src.api as api
    from src.api.schemas.auth import RegisterRequest

    captured_reg = {}
    captured_sess = {}

    fake_user = SimpleNamespace(
        id=123,
        username="m1reg",
        email="m1reg@example.com",
        avatar_url=None,
        created_at=0,
        email_verified=False,
        totp_enabled=False,
        age_verified=False,
        badges=[],
        deletion_status="active",
        deletion_at=None,
    )

    class _SessResult:
        token = "tok"
        # Mirrors AuthResult.session (Optional[Session]) — see _Result above.
        session = None

    fake_auth = MagicMock()

    def _register(**kwargs):
        captured_reg.update(kwargs)
        return fake_user

    def _sess(**kwargs):
        captured_sess.update(kwargs)
        return _SessResult()

    fake_auth.register.side_effect = _register
    fake_auth.create_session_for_user.side_effect = _sess

    orig = api.get_auth
    api.get_auth = lambda: fake_auth
    orig_features = api.get_features
    api.get_features = lambda: None
    try:
        asyncio.run(
            reg_mod.register(
                request,
                Response(),
                RegisterRequest(
                    username=username,
                    email=f"{username}@example.com",
                    password="TestPass123!",
                ),
            )
        )
    finally:
        api.get_auth = orig
        api.get_features = orig_features
    return captured_reg.get("ip_address"), captured_sess.get("ip_address")


def test_register_route_uses_proxy_aware_ip():
    """Register must forward the proxy-resolved IP to register + session."""
    config.set("api", {"trusted_proxies": ["127.0.0.1"]})
    reg_ip, sess_ip = _register_route_ips(
        _make_request("127.0.0.1", "203.0.113.7"), username="m1regA"
    )
    assert reg_ip == "203.0.113.7"
    assert sess_ip == "203.0.113.7"


def test_register_route_ignores_spoofed_xff():
    config.set("api", {"trusted_proxies": ["10.0.0.0/8"]})
    reg_ip, _ = _register_route_ips(
        _make_request("203.0.113.9", "198.51.100.7"), username="m1regB"
    )
    assert reg_ip == "203.0.113.9"
