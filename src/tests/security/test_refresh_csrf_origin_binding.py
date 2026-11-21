"""Regression: session refresh must enforce the full cookie-CSRF policy.

``POST /api/v1/auth/refresh`` rotates the session token, so it is a
state-changing cookie-authenticated request. Unlike every other mutation it
read the session cookie directly instead of going through ``get_current_user``,
so the auth middleware's CSRF rejection (advisory ``scope['state']['auth_error']``
only) never actually stopped it. The route guarded itself with just the static,
guessable ``X-Plexichat-Request: 1`` marker and no ``Sec-Fetch-Site`` /
``Origin`` / ``Referer`` binding -- exactly the gap the perimeter's
Origin/Referer check exists to close. A cross-site, cookie-bearing mutation
carrying the marker was therefore accepted and rotated the victim's session.
"""

import uuid
from unittest.mock import patch

import pytest

from src.utils import encryption


def _mk_user(test_client):
    username = f"csrf_refresh_{uuid.uuid4().hex[:8]}"
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


def _login(test_client, username):
    r = test_client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "TestPass123!"},
    )
    assert r.status_code == 200, r.text
    return r


class TestRefreshCsrfOriginBinding:
    def test_cross_site_request_with_marker_is_rejected(self, test_client):
        username = _mk_user(test_client)
        _login(test_client, username)

        # A cross-site attacker page that managed to attach the static marker
        # (permissive CORS origin, header-injecting proxy/extension) must not
        # be able to rotate the victim's session. The middleware only records
        # advisory state for this route, so the route itself must fail closed.
        r = test_client.post(
            "/api/v1/auth/refresh",
            headers={
                "X-Plexichat-Request": "1",
                "Sec-Fetch-Site": "cross-site",
                "Origin": "https://evil.example",
            },
        )
        assert r.status_code == 403, r.text

    def test_same_site_with_marker_but_unlisted_origin_is_rejected(self, test_client):
        username = _mk_user(test_client)
        _login(test_client, username)

        # ``same-site`` is not a CSRF boundary; the marker alone is a guessable
        # constant, so an unlisted Origin must still be rejected.
        r = test_client.post(
            "/api/v1/auth/refresh",
            headers={
                "X-Plexichat-Request": "1",
                "Sec-Fetch-Site": "same-site",
                "Origin": "https://evil.example",
            },
        )
        assert r.status_code == 403, r.text

    def test_same_origin_request_with_marker_still_succeeds(self, test_client):
        username = _mk_user(test_client)
        _login(test_client, username)

        # Legitimate same-origin browser request keeps working.
        r = test_client.post(
            "/api/v1/auth/refresh",
            headers={
                "X-Plexichat-Request": "1",
                "Sec-Fetch-Site": "same-origin",
            },
        )
        assert r.status_code == 200, r.text


class TestRefreshCsrfHelper:
    """The route-level helper mirrors the middleware's fail-closed policy."""

    @staticmethod
    def _req(headers):
        return type("_Req", (), {"headers": headers})()

    def _allowed(self, headers, cors=("http://testserver",)):
        from src.api.routes.auth.sessions import _cookie_csrf_allowed

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "utils.config.get",
                lambda key, *a, **k: (
                    {"cors_origins": list(cors)} if key == "api" else {}
                ),
            )
            return _cookie_csrf_allowed(self._req(headers))

    def test_cross_site_never_allowed_even_with_marker(self):
        assert (
            self._allowed(
                {
                    "Sec-Fetch-Site": "cross-site",
                    "X-Plexichat-Request": "1",
                    "Origin": "http://testserver",
                }
            )
            is False
        )

    def test_same_site_binds_origin_allowlist(self):
        assert (
            self._allowed(
                {
                    "Sec-Fetch-Site": "same-site",
                    "Origin": "https://evil.example",
                }
            )
            is False
        )
        assert (
            self._allowed(
                {
                    "Sec-Fetch-Site": "same-site",
                    "Origin": "http://testserver",
                }
            )
            is True
        )

    def test_missing_fetch_metadata_without_origin_is_non_browser(self):
        # No Origin and no Referer -> non-browser client; the route's separate
        # marker-header check remains the only browser-agnostic gate.
        assert self._allowed({}) is True
