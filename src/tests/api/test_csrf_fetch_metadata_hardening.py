"""Regression tests for CSRF handling of Sec-Fetch-Site fetch metadata.

Only ``same-origin`` is an exact-origin signal a cross-origin page cannot
forge. ``same-site`` still admits any sibling subdomain under the same
registrable domain, and ``none`` is emitted for initiator-less navigations and
non-fetch contexts. Treating either as implicitly trusted let a credentialed
mutating request skip the ``X-Plexichat-Request`` marker and the Origin/Referer
allowlist -- a real CSRF hole. These cases must keep requiring both checks.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


def _run(extra_headers, method="POST", path="/api/v1/foo"):
    from src.api.middleware.authentication import AuthenticationMiddleware

    headers = [
        (b"host", b"testserver"),
        (b"cookie", b"plexichat_token=abc123"),
    ]
    for name, value in extra_headers:
        headers.append((name, value))

    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "headers": headers,
        "query_string": b"",
        "client": ("testclient", 50000),
        "scheme": "http",
        "server": ("testserver", 80),
    }

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    messages = []

    async def send(message):
        messages.append(message)

    async def inner(scope, receive_, send_):
        await send_({"type": "http.response.start", "status": 200, "headers": []})
        await send_({"type": "http.response.body", "body": b"ok"})

    fake_auth = MagicMock()
    fake_auth.verify_token.return_value = SimpleNamespace(user_id=7)
    fake_auth.is_api_access_token_required.return_value = False

    with (
        patch("src.api.is_self_test_request", return_value=False),
        patch("src.api.get_auth", return_value=fake_auth),
        patch("src.api.get_db", return_value=None),
    ):
        asyncio.run(AuthenticationMiddleware(inner)(scope, receive, send))
    return scope, fake_auth


class TestFetchMetadataCsrf:
    def test_same_site_without_marker_rejected(self):
        scope, fake_auth = _run([(b"sec-fetch-site", b"same-site")])
        assert scope["state"]["auth_error"] == "CSRF validation failed"
        fake_auth.verify_token.assert_not_called()

    def test_same_site_with_marker_but_unlisted_origin_rejected(self):
        scope, fake_auth = _run(
            [
                (b"sec-fetch-site", b"same-site"),
                (b"x-plexichat-request", b"1"),
                (b"origin", b"https://evil.example"),
            ]
        )
        assert scope["state"]["auth_error"] == "CSRF validation failed"
        fake_auth.verify_token.assert_not_called()

    def test_same_site_with_marker_and_allowed_origin_accepted(self):
        scope, fake_auth = _run(
            [
                (b"sec-fetch-site", b"same-site"),
                (b"x-plexichat-request", b"1"),
                (b"origin", b"http://testserver"),
            ]
        )
        assert "auth_error" not in scope["state"]
        fake_auth.verify_token.assert_called_once()
        assert scope["state"]["cookie_auth"] is True

    def test_none_without_marker_rejected(self):
        scope, fake_auth = _run([(b"sec-fetch-site", b"none")])
        assert scope["state"]["auth_error"] == "CSRF validation failed"
        fake_auth.verify_token.assert_not_called()

    def test_none_with_marker_and_allowed_referer_accepted(self):
        scope, fake_auth = _run(
            [
                (b"sec-fetch-site", b"none"),
                (b"x-plexichat-request", b"1"),
                (b"referer", b"http://testserver/settings"),
            ]
        )
        assert "auth_error" not in scope["state"]
        fake_auth.verify_token.assert_called_once()
        assert scope["state"]["cookie_auth"] is True

    def test_unknown_fetch_site_value_requires_marker(self):
        scope, fake_auth = _run([(b"sec-fetch-site", b"some-future-value")])
        assert scope["state"]["auth_error"] == "CSRF validation failed"
        fake_auth.verify_token.assert_not_called()

    def test_exact_same_origin_remains_trusted_without_marker(self):
        # Documents the one value deliberately left as an unforgeable signal:
        # an exact same-origin request cannot be produced by a cross-origin page.
        scope, fake_auth = _run([(b"sec-fetch-site", b"same-origin")])
        assert "auth_error" not in scope["state"]
        fake_auth.verify_token.assert_called_once()
