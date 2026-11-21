"""Regression: webhook tokens must never reach the HTTP access log.

``POST /webhooks/{webhook_id}/{token}`` carries a live webhook secret in the
URL *path*. ``LoggingMiddleware`` used to log ``scope["path"]`` (the resolved
path) verbatim, so every execution wrote the token to ``latest.log``. The fix
logs the matched route template instead, and redacts the token segment when
routing metadata is unavailable. These tests drive the real ASGI middleware
and assert on the exact log lines it emits.
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from src.api.middleware.logging import LoggingMiddleware

pytestmark = [pytest.mark.security, pytest.mark.unit]

CANARY = "CANARYWEBHOOKSECRET987"
RAW_PATH = f"/api/v1/webhooks/424242/webhook.424242.{CANARY}"
TEMPLATE = "/api/v1/webhooks/{webhook_id}/{token}"


class _Route:
    path_format = TEMPLATE
    path = TEMPLATE


def _scope(*, with_route: bool = True) -> dict:
    scope = {
        "type": "http",
        "method": "POST",
        "path": RAW_PATH,
        "headers": [(b"host", b"testserver")],
        "client": ("127.0.0.1", 1234),
        "query_string": b"",
        "scheme": "http",
        "server": ("testserver", 80),
    }
    if with_route:
        scope["route"] = _Route()
    return scope


async def _receive():
    return {"type": "http.request", "body": b"", "more_body": False}


async def _send(message):
    return None


async def _ok_app(scope, receive, send):
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok", "more_body": False})


def _run(scope, inner=_ok_app, *, raises: bool = False):
    captured: list[str] = []
    with (
        patch("src.api.middleware.logging._log_info", side_effect=captured.append),
        patch("src.api.middleware.logging._log_warning", side_effect=captured.append),
        patch("src.api.middleware.logging._log_error", side_effect=captured.append),
    ):
        if raises:
            with pytest.raises(RuntimeError):
                asyncio.run(LoggingMiddleware(inner)(scope, _receive, _send))
        else:
            asyncio.run(LoggingMiddleware(inner)(scope, _receive, _send))
    return captured


def test_access_log_uses_route_template_not_resolved_path():
    """A matched route logs its template; the token-bearing path is not logged."""
    captured = _run(_scope())

    assert captured, "middleware must emit an access log line"
    rendered = "\n".join(captured)
    assert CANARY not in rendered, "webhook token leaked into the access log"
    assert TEMPLATE in rendered, (
        "access log should use the route template (non-vacuous check that a "
        f"log line was emitted and normalized): {rendered}"
    )


def test_access_log_redacts_webhook_token_without_route_metadata():
    """A 404 (no matched route) must also avoid emitting the webhook token."""
    captured = _run(_scope(with_route=False))

    assert captured, "middleware must emit an access log line"
    rendered = "\n".join(captured)
    assert CANARY not in rendered, "webhook token leaked into the access log"
    assert "/webhooks/424242/{token}" in rendered, (
        f"expected the token segment to be redacted: {rendered}"
    )


def test_error_access_log_does_not_leak_webhook_token():
    """The exception path must not write the token either."""

    async def boom(scope, receive, send):
        raise RuntimeError("boom")

    captured = _run(_scope(), inner=boom, raises=True)

    assert captured, "middleware must emit an error log line"
    rendered = "\n".join(captured)
    assert CANARY not in rendered, "webhook token leaked into the error access log"
    assert "RuntimeError" in rendered
