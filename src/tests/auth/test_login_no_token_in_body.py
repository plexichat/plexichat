"""
M9 regression: session tokens must not appear in JSON bodies on cookie flows.

Pre-fix, POST /auth/login (and /auth/register, /auth/refresh) returned the
raw session token in the JSON body AND set it as an HttpOnly cookie, so any
XSS payload could read the body token and the HttpOnly protection was
defeated. Post-fix, cookie-flow bodies carry only a status marker plus the
session lifetime (``expires_in``); the credential travels solely in the
``Set-Cookie`` header. A raw body token is returned SOLELY for explicit
non-browser opt-in via ``X-Plexichat-Token-Mode: bearer``.

Each test below FAILS on pre-fix code (body ``token`` was the raw secret)
and PASSES after the fix.
"""

import uuid
from unittest.mock import patch

BEARER_OPT_IN = {"X-Plexichat-Token-Mode": "bearer"}


def _mk_user_via_api(test_client, username=None):
    """Register a user through the HTTP API (exercises the real route)."""
    from src.utils import encryption

    username = username or f"m9_{uuid.uuid4().hex[:8]}"
    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        r = test_client.post(
            "/api/v1/auth/register",
            json={
                "username": username,
                "email": f"{username}@example.com",
                "password": "TestPass123!",
            },
        )
    assert r.status_code == 200, r.text
    return username


def _cookie_value(test_client):
    return test_client.cookies.get("plexichat_token")


class TestLoginNoTokenInBody:
    def test_login_cookie_flow_has_no_token_in_body(self, test_client):
        username = _mk_user_via_api(test_client)
        test_client.cookies.clear()

        r = test_client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": "TestPass123!"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "success"
        # The raw session token MUST NOT be in the JSON body...
        assert body.get("token") is None
        # ...but a marker/expiry is still returned...
        assert body.get("expires_in") and body["expires_in"] > 0
        assert body.get("user", {}).get("username") == username
        # ...and the credential travels ONLY in the HttpOnly cookie.
        set_cookie = r.headers.get("set-cookie", "")
        assert "plexichat_token=" in set_cookie
        assert "httponly" in set_cookie.lower()
        cookie_val = _cookie_value(test_client)
        assert cookie_val, "login must set the plexichat_token cookie"
        assert cookie_val not in r.text

    def test_cookie_auth_works_without_body_token(self, test_client):
        username = _mk_user_via_api(test_client)
        test_client.cookies.clear()
        r = test_client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": "TestPass123!"},
        )
        assert r.status_code == 200, r.text
        assert r.json().get("token") is None

        # Cookie-only request (no Authorization header) authenticates.
        me = test_client.get("/api/v1/users/@me")
        assert me.status_code == 200, me.text
        assert me.json()["username"] == username

    def test_explicit_opt_in_returns_body_token(self, test_client):
        username = _mk_user_via_api(test_client)
        test_client.cookies.clear()

        r = test_client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": "TestPass123!"},
            headers=BEARER_OPT_IN,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body.get("token"), "opt-in must still yield a body bearer"
        # The opted-in body token matches the cookie credential...
        assert body["token"] == _cookie_value(test_client)
        # ...and the cookie is still set for defence in depth.
        assert "plexichat_token=" in r.headers.get("set-cookie", "")

    def test_register_cookie_flow_has_no_token_in_body(self, test_client):
        from src.utils import encryption

        username = f"m9reg_{uuid.uuid4().hex[:8]}"
        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            r = test_client.post(
                "/api/v1/auth/register",
                json={
                    "username": username,
                    "email": f"{username}@example.com",
                    "password": "TestPass123!",
                },
            )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body.get("token") is None
        assert body.get("expires_in") and body["expires_in"] > 0
        set_cookie = r.headers.get("set-cookie", "")
        assert "plexichat_token=" in set_cookie
        assert "httponly" in set_cookie.lower()
        cookie_val = _cookie_value(test_client)
        assert cookie_val
        assert cookie_val not in r.text

    def test_refresh_cookie_flow_has_no_token_in_body(self, test_client):
        username = _mk_user_via_api(test_client)
        test_client.cookies.clear()
        login = test_client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": "TestPass123!"},
        )
        assert login.status_code == 200, login.text

        r = test_client.post(
            "/api/v1/auth/refresh", headers={"X-Plexichat-Request": "1"}
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "success"
        assert body.get("token") is None
        assert body.get("expires_in") and body["expires_in"] > 0
        set_cookie = r.headers.get("set-cookie", "")
        assert "plexichat_token=" in set_cookie
        new_cookie_val = _cookie_value(test_client)
        assert new_cookie_val
        assert new_cookie_val not in r.text
