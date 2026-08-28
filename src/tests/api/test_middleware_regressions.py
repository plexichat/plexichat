"""
Regression tests for middleware cluster fixes.

Covers:
- IP-blocking middleware actually blocking blacklisted peers (H3)
- Logging middleware wired and not leaking exception strings (M1)
- CSRF defense against browser-set Sec-Fetch-Site: cross-site (M2)
- Auth gate and public endpoints honouring the configurable api_prefix (M3)
- Third-party exception details not echoed to clients (M6)
- HSTS security header present on responses (L1)
- Database cleanup failures surfaced instead of silently swallowed (L3)
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.api.middleware.error_handling import _safe_error_message


class TestIPBlockingMiddleware:
    """H3: blacklisted IPs must be rejected at the perimeter."""

    def _run(self, blocked, ip="203.0.113.9"):
        from src.api.middleware.ip_blocking import IPBlockingMiddleware

        messages = []

        async def send(message):
            messages.append(message)

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def inner(scope, receive_, send_):
            await send_({"type": "http.response.start", "status": 200, "headers": []})
            await send_({"type": "http.response.body", "body": b"ok"})

        scope = {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [(b"host", b"testserver")],
            "client": (ip, 1234),
            "query_string": b"",
            "scheme": "http",
            "server": ("testserver", 80),
        }
        fake_auth = MagicMock()
        fake_auth.is_ip_blocked.return_value = blocked
        with patch("src.api.get_auth", return_value=fake_auth):
            import asyncio

            asyncio.run(IPBlockingMiddleware(inner)(scope, receive, send))
        return messages

    def test_blacklisted_ip_receives_403(self):
        messages = self._run(blocked=True)
        starts = [m for m in messages if m["type"] == "http.response.start"]
        assert starts[0]["status"] == 403
        assert b"blocked" in messages[-1]["body"].lower()

    def test_allowed_ip_reaches_app(self):
        messages = self._run(blocked=False)
        starts = [m for m in messages if m["type"] == "http.response.start"]
        assert starts[0]["status"] == 200


class TestLoggingMiddlewareSanitization:
    """M1: exception strings must not leak into request logs."""

    @pytest.mark.asyncio
    async def test_exception_logs_class_name_not_message(self):
        from src.api.middleware.logging import LoggingMiddleware

        captured = []

        def capture(msg):
            captured.append(msg)

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            return None

        async def inner(scope, receive_, send_):
            raise ValueError("sensitive db password in message")

        scope = {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/boom",
            "headers": [(b"host", b"testserver")],
            "client": ("127.0.0.1", 1234),
            "query_string": b"",
            "scheme": "http",
            "server": ("testserver", 80),
        }

        with (
            patch("src.api.middleware.logging._log_error", side_effect=capture),
            pytest.raises(ValueError),
        ):
            await LoggingMiddleware(inner)(scope, receive, send)

        assert captured, "error path must log"
        assert "sensitive db password" not in captured[0]
        assert "ValueError" in captured[0]


class TestCsrfSecFetchSite:
    """M2: browser-set Sec-Fetch-Site: cross-site must fail CSRF."""

    def _scope(self, extra_headers):
        headers = [
            (b"host", b"testserver"),
            (b"cookie", b"plexichat_token=abc123"),
        ]
        for name, value in extra_headers:
            headers.append((name, value))
        return {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/foo",
            "headers": headers,
            "query_string": b"",
            "client": ("testclient", 50000),
            "scheme": "http",
            "server": ("testserver", 80),
        }

    def _run(self, extra_headers):
        from src.api.middleware.authentication import AuthenticationMiddleware

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

        scope = self._scope(extra_headers)
        with (
            patch("src.api.is_self_test_request", return_value=False),
            patch("src.api.get_auth", return_value=fake_auth),
            patch("src.api.get_db", return_value=None),
        ):
            import asyncio

            asyncio.run(AuthenticationMiddleware(inner)(scope, receive, send))
        return scope, fake_auth

    def test_cross_site_fetch_with_cookie_rejected_even_with_marker(self):
        scope, _ = self._run(
            [
                (b"sec-fetch-site", b"cross-site"),
                (b"x-plexichat-request", b"1"),
            ]
        )
        assert scope["state"]["auth_error"] == "CSRF validation failed"

    def test_cross_site_fetch_without_marker_rejected(self):
        scope, _ = self._run([(b"sec-fetch-site", b"cross-site")])
        assert scope["state"]["auth_error"] == "CSRF validation failed"

    def test_same_origin_with_marker_allowed(self):
        scope, fake_auth = self._run(
            [
                (b"sec-fetch-site", b"same-origin"),
                (b"x-plexichat-request", b"1"),
            ]
        )
        assert "auth_error" not in scope["state"]
        fake_auth.verify_token.assert_called_once()
        assert scope["state"]["user"].user_id == 7

    def test_no_sec_fetch_site_falls_back_to_marker_requirement(self):
        # Non-browser clients omit Sec-Fetch-Site; the marker header must
        # still be required for cookie-authenticated mutations.
        scope, _ = self._run([])
        assert scope["state"]["auth_error"] == "CSRF validation failed"


class TestAuthPrefixScoping:
    """M3: public endpoints and gates must follow the configurable prefix."""

    def _run(self, path, api_prefix="/custom"):
        from src.api.middleware.authentication import AuthenticationMiddleware

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        messages = []

        async def send(message):
            messages.append(message)

        async def inner(scope, receive_, send_):
            await send_({"type": "http.response.start", "status": 200, "headers": []})
            await send_({"type": "http.response.body", "body": b"ok"})

        scope = {
            "type": "http",
            "method": "GET",
            "path": path,
            "headers": [(b"host", b"testserver")],
            "query_string": b"",
            "client": ("testclient", 50000),
            "scheme": "http",
            "server": ("testserver", 80),
        }
        config_obj = SimpleNamespace(api_prefix=api_prefix)
        auth_mock = MagicMock()
        auth_mock.is_api_access_token_required.return_value = False
        with (
            patch("src.api.is_self_test_request", return_value=False),
            patch("src.api.config.get_api_config", return_value=config_obj),
            patch("src.api.get_auth", return_value=auth_mock),
            patch("src.api.get_db", return_value=None),
        ):
            import asyncio

            asyncio.run(AuthenticationMiddleware(inner)(scope, receive, send))
        return scope, messages, auth_mock

    def test_public_login_on_custom_prefix_is_not_auth_gated(self):
        scope, messages, auth_mock = self._run("/custom/auth/login")
        assert "auth_error" not in scope["state"]
        starts = [m for m in messages if m["type"] == "http.response.start"]
        assert starts[0]["status"] == 200
        # Public short-circuit: the auth gate (and thus get_auth) never runs.
        auth_mock.is_api_access_token_required.assert_not_called()

    def test_protected_route_on_custom_prefix_is_auth_gated(self):
        # The custom-prefix route must NOT be treated as public: the auth
        # gate must run (proving prefix-scoped enforcement), unlike the
        # public login path above where it never executes.
        scope, _, auth_mock = self._run("/custom/users/@me")
        auth_mock.is_api_access_token_required.assert_called_once()
        assert scope["state"].get("auth_error") is None


class TestExceptionMessageLeakage:
    """M6: third-party exception strings must not reach clients."""

    def test_third_party_mapped_exception_gets_generic_message(self):
        class SecretValidationError(Exception):
            pass

        SecretValidationError.__module__ = "some_third_party_library"
        exc = SecretValidationError("postgres://user:secret@db:5432/internal")
        assert _safe_error_message(exc, 400) == "Invalid request"

    def test_third_party_unmapped_exception_gets_generic_500(self):
        class OopsError(Exception):
            pass

        OopsError.__module__ = "some_third_party_library"
        assert _safe_error_message(OopsError("traceback fodder"), 500) == (
            "Internal server error"
        )

    def test_project_exception_message_preserved(self):
        class ProjectValidationError(Exception):
            pass

        ProjectValidationError.__module__ = "src.core.something"
        assert (
            _safe_error_message(ProjectValidationError("Username taken"), 400)
            == "Username taken"
        )

    def test_general_handler_does_not_leak_third_party_details(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from src.api.middleware.error_handling import setup_exception_handlers

        app = FastAPI()
        setup_exception_handlers(app)

        class SecretValidationError(Exception):
            pass

        SecretValidationError.__module__ = "third_party_lib"

        @app.get("/boom")
        async def boom():
            raise SecretValidationError("postgres://user:secret@db:5432/internal")

        # Starlette's ServerErrorMiddleware always re-raises the exception
        # after the handler responds (so servers can log it); the test client
        # only surfaces the response when told not to re-raise.
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/boom")

        assert resp.status_code == 400
        assert "postgres" not in resp.text
        assert "secret" not in resp.text
        assert resp.json()["error"]["message"] == "Invalid request"


class TestHstsHeader:
    """L1: responses must carry Strict-Transport-Security."""

    def test_hsts_header_present_on_api_response(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from src.api.middleware.security import SecurityHeadersMiddleware

        app = FastAPI()
        app.add_middleware(SecurityHeadersMiddleware)

        @app.get("/api/v1/test")
        async def api_test():
            return {"ok": True}

        headers = TestClient(app).get("/api/v1/test").headers
        assert headers["strict-transport-security"].startswith("max-age=")
        assert "includeSubDomains" in headers["strict-transport-security"]


class TestDatabaseMiddlewareObservability:
    """L3: cleanup failures must be surfaced, not swallowed."""

    @pytest.mark.asyncio
    async def test_cleanup_failure_is_logged(self):
        from src.api.middleware.database import DatabaseMiddleware

        warnings = []

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            return None

        async def inner(scope, receive_, send_):
            return None

        failing_db = MagicMock()
        failing_db.type = "sqlite"
        failing_db.in_transaction = True
        failing_db.rollback.side_effect = RuntimeError("disk I/O error")

        scope = {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/test",
            "headers": [(b"host", b"testserver")],
            "client": ("testclient", 50000),
            "query_string": b"",
            "scheme": "http",
            "server": ("testserver", 80),
        }
        with (
            patch("src.api.get_db", return_value=failing_db),
            patch(
                "utils.logger.warning",
                side_effect=lambda msg: warnings.append(msg),
            ),
        ):
            await DatabaseMiddleware(inner)(scope, receive, send)

        assert warnings, "cleanup failure must be logged"
        assert "disk I/O error" in warnings[0]
