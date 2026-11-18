"""Regression: every response path must carry an unweakable security policy.

The stamping middleware used to "add if absent", which let a handler or a
reflected value downgrade nosniff/referrer/framing/CSP. It also let a 500 from
Starlette's outer error middleware escape without any headers, and did nothing
about a handler echoing an arbitrary ``Origin`` into CORS headers.
"""

from typing import Any, Dict, List, Tuple

import pytest

from src.api.middleware import security as security_module
from src.api.middleware.security import SecurityHeadersMiddleware
from src.api.middleware.response_policy import (
    CSP_API,
    CSP_MEDIA,
    HSTS_VALUE,
    PERMISSIONS_POLICY_VALUE,
)

Header = Tuple[bytes, bytes]


def _scope(path: str) -> Dict[str, Any]:
    return {
        "type": "http",
        "path": path,
        "method": "GET",
        "headers": [],
        "query_string": b"",
        "client": ("10.0.0.1", 1234),
        "server": ("testserver", 80),
        "scheme": "http",
    }


def _values(headers: List[Header], name: bytes) -> List[bytes]:
    return [value for key, value in headers if key.lower() == name]


async def _run(
    path: str,
    response_headers: List[Header],
    *,
    error: bool = False,
    monkeypatch: pytest.MonkeyPatch,
) -> List[Header]:
    # One config read now feeds every policy input; stub that single seam.
    monkeypatch.setattr(
        security_module,
        "_security_policy",
        lambda: ("/api/v1", ("https://good.example",), True),
    )

    async def downstream(scope, receive, send):
        if error:
            raise RuntimeError("downstream exploded")
        await send(
            {"type": "http.response.start", "status": 200, "headers": response_headers}
        )
        await send({"type": "http.response.body", "body": b"ok"})

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    messages: List[Dict[str, Any]] = []

    async def send(message: Any) -> None:
        messages.append(message)

    await SecurityHeadersMiddleware(downstream)(_scope(path), receive, send)
    start = next(
        message for message in messages if message["type"] == "http.response.start"
    )
    return list(start["headers"])


class TestResponsePolicyEnforcement:
    @pytest.mark.asyncio
    async def test_weakened_headers_are_replaced_on_api_paths(self, monkeypatch):
        final = await _run(
            "/api/v1/users",
            [
                (b"X-Content-Type-Options", b"sniff"),
                (b"Referrer-Policy", b"unsafe-url"),
                (b"X-Frame-Options", b"ALLOWALL"),
                (b"Content-Security-Policy", b"script-src *"),
                (b"Strict-Transport-Security", b"max-age=0"),
                (b"Permissions-Policy", b"camera=*"),
            ],
            monkeypatch=monkeypatch,
        )

        assert _values(final, b"x-content-type-options") == [b"nosniff"]
        assert _values(final, b"referrer-policy") == [
            b"strict-origin-when-cross-origin"
        ]
        assert _values(final, b"x-frame-options") == [b"SAMEORIGIN"]
        assert _values(final, b"content-security-policy") == [CSP_API]
        assert _values(final, b"strict-transport-security") == [HSTS_VALUE]
        assert _values(final, b"permissions-policy") == [PERMISSIONS_POLICY_VALUE]

    @pytest.mark.asyncio
    async def test_duplicate_headers_are_collapsed(self, monkeypatch):
        final = await _run(
            "/api/v1/users",
            [
                (b"X-Content-Type-Options", b"sniff"),
                (b"X-Content-Type-Options", b"nosniff"),
                (b"Referrer-Policy", b"unsafe-url"),
            ],
            monkeypatch=monkeypatch,
        )

        names = [key.lower() for key, _ in final]
        assert names.count(b"x-content-type-options") == 1
        assert _values(final, b"x-content-type-options") == [b"nosniff"]

    @pytest.mark.asyncio
    async def test_reflected_cors_origin_is_stripped(self, monkeypatch):
        final = await _run(
            "/api/v1/users",
            [
                (b"Access-Control-Allow-Origin", b"https://evil.example"),
                (b"Access-Control-Allow-Credentials", b"true"),
            ],
            monkeypatch=monkeypatch,
        )

        assert _values(final, b"access-control-allow-origin") == []
        assert _values(final, b"access-control-allow-credentials") == []

    @pytest.mark.asyncio
    async def test_allowlisted_cors_origin_is_preserved(self, monkeypatch):
        final = await _run(
            "/api/v1/users",
            [(b"Access-Control-Allow-Origin", b"https://good.example")],
            monkeypatch=monkeypatch,
        )

        assert _values(final, b"access-control-allow-origin") == [
            b"https://good.example"
        ]

    @pytest.mark.asyncio
    async def test_wildcard_origin_with_credentials_is_stripped(self, monkeypatch):
        final = await _run(
            "/api/v1/users",
            [
                (b"Access-Control-Allow-Origin", b"*"),
                (b"Access-Control-Allow-Credentials", b"true"),
            ],
            monkeypatch=monkeypatch,
        )

        assert _values(final, b"access-control-allow-origin") == []

    @pytest.mark.asyncio
    async def test_media_path_uses_media_csp(self, monkeypatch):
        final = await _run(
            "/api/v1/media/123",
            [(b"X-Frame-Options", b"ALLOWALL")],
            monkeypatch=monkeypatch,
        )

        assert _values(final, b"content-security-policy") == [CSP_MEDIA]
        assert _values(final, b"x-frame-options") == [b"SAMEORIGIN"]

    @pytest.mark.asyncio
    async def test_error_path_is_stamped_too(self, monkeypatch):
        final = await _run("/api/v1/users", [], error=True, monkeypatch=monkeypatch)

        assert _values(final, b"x-content-type-options") == [b"nosniff"]
        assert _values(final, b"content-security-policy") == [CSP_API]
