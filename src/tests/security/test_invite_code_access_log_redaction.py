"""Regression: invite codes must never reach the HTTP access log.

``GET /invite/{code}`` is handled by the SPA ``StaticClientMiddleware``, not by
a FastAPI route, so ``scope["route"]`` is absent. The access-log sanitizer
``_safe_log_path`` only redacts the webhook token segment; an invite link (a
bearer credential that grants server membership) is therefore written verbatim
to ``latest.log`` on every visit. This test drives the real ASGI middleware and
asserts on the exact log line it emits.
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from src.api.middleware.logging import LoggingMiddleware

pytestmark = [pytest.mark.security, pytest.mark.unit]

CANARY = "CANARYINVITECODE987654"
RAW_PATH = f"/invite/{CANARY}"
REDACTED_TEMPLATE = "/invite/{code}"


def _scope() -> dict:
    # Deliberately no "route": the SPA ASGI middleware handles /invite/{code}.
    return {
        "type": "http",
        "method": "GET",
        "path": RAW_PATH,
        "headers": [(b"host", b"testserver")],
        "client": ("127.0.0.1", 1234),
        "query_string": b"",
        "scheme": "http",
        "server": ("testserver", 80),
    }


async def _receive():
    return {"type": "http.request", "body": b"", "more_body": False}


async def _send(message):
    return None


async def _redirect_app(scope, receive, send):
    # Mirrors StaticClientMiddleware's 302 to /app.html?invite=<code>.
    await send(
        {
            "type": "http.response.start",
            "status": 302,
            "headers": [(b"location", b"/app.html?invite=" + CANARY.encode())],
        }
    )
    await send({"type": "http.response.body", "body": b"", "more_body": False})


def _run() -> list[str]:
    captured: list[str] = []
    with (
        patch("src.api.middleware.logging._log_info", side_effect=captured.append),
        patch("src.api.middleware.logging._log_warning", side_effect=captured.append),
        patch("src.api.middleware.logging._log_error", side_effect=captured.append),
    ):
        asyncio.run(LoggingMiddleware(_redirect_app)(_scope(), _receive, _send))
    return captured


def test_invite_code_is_not_written_to_access_log():
    captured = _run()

    assert captured, "middleware must emit an access log line"
    rendered = "\n".join(captured)
    assert CANARY not in rendered, "invite code leaked into the access log"
    assert REDACTED_TEMPLATE in rendered, (
        "access log should use a redacted template instead of the raw invite "
        f"path (non-vacuous check that a normalized log line was emitted): "
        f"{rendered}"
    )
