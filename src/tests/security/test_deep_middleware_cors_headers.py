"""Deep hardening regression: CORS credential policy and perimeter headers.

Three real gaps are covered here:

1. ``ErrorHandlingMiddleware._get_cors_headers`` hard-coded
   ``Access-Control-Allow-Credentials: true`` on every error response for any
   configured origin, ignoring ``api.cors_allow_credentials``. An operator who
   disabled credentialed CORS still granted it on 400/401/403/5xx responses.
2. ``response_policy._enforce_cors`` validated only the reflected origin and
   never removed a handler-injected ``Access-Control-Allow-Credentials`` when
   credentials were disabled. With a wildcard allowlist it even kept a
   credentialed reflection of an arbitrary origin.
3. ``IPBlockingMiddleware`` is the outermost middleware, so its locally
   generated 403 bypassed ``SecurityHeadersMiddleware`` entirely and reached
   clients with no security headers at all.
"""

from typing import Any, Dict, List, Tuple

import pytest

from src.api.middleware.error_handling import _get_cors_headers
from src.api.middleware.ip_blocking import IPBlockingMiddleware
from src.api.middleware.response_policy import enforce_response_policy

Header = Tuple[bytes, bytes]


def _headers(message: Dict[str, Any]) -> List[Header]:
    return list(message.get("headers", []))


def _values(headers: List[Header], name: bytes) -> List[bytes]:
    return [v for k, v in headers if k.lower() == name]


class _Request:
    def __init__(self, origin: str):
        self.headers = {"origin": origin}


# --- 1. Error responses must honour the credential policy ------------------


def test_error_cors_omits_credentials_when_disabled(monkeypatch):
    def fake_get(key, default=None):
        if key == "api":
            return {
                "cors_origins": ["https://good.example"],
                "cors_allow_credentials": False,
            }
        return default

    import utils.config as config

    monkeypatch.setattr(config, "get", fake_get)

    headers = _get_cors_headers(_Request("https://good.example"))
    assert headers.get("Access-Control-Allow-Origin") == "https://good.example"
    assert "Access-Control-Allow-Credentials" not in headers


def test_error_cors_includes_credentials_when_enabled(monkeypatch):
    def fake_get(key, default=None):
        if key == "api":
            return {
                "cors_origins": ["https://good.example"],
                "cors_allow_credentials": True,
            }
        return default

    import utils.config as config

    monkeypatch.setattr(config, "get", fake_get)

    headers = _get_cors_headers(_Request("https://good.example"))
    assert headers.get("Access-Control-Allow-Credentials") == "true"


def test_error_cors_never_reflects_unlisted_origin(monkeypatch):
    def fake_get(key, default=None):
        if key == "api":
            return {
                "cors_origins": ["https://good.example"],
                "cors_allow_credentials": True,
            }
        return default

    import utils.config as config

    monkeypatch.setattr(config, "get", fake_get)

    assert _get_cors_headers(_Request("https://evil.example")) == {}


# --- 2. Response policy strips credentials when the policy disables them ---


def test_response_policy_drops_credentials_when_disabled():
    final = enforce_response_policy(
        [
            (b"Access-Control-Allow-Origin", b"https://good.example"),
            (b"Access-Control-Allow-Credentials", b"true"),
        ],
        path="/api/v1/users",
        api_prefix="/api/v1",
        cors_origins=["https://good.example"],
        cors_allow_credentials=False,
    )
    assert _values(final, b"access-control-allow-origin") == [
        b"https://good.example"
    ]
    assert _values(final, b"access-control-allow-credentials") == []


def test_response_policy_rejects_credentialed_wildcard_reflection():
    final = enforce_response_policy(
        [
            (b"Access-Control-Allow-Origin", b"https://attacker.example"),
            (b"Access-Control-Allow-Credentials", b"true"),
        ],
        path="/api/v1/users",
        api_prefix="/api/v1",
        cors_origins=["*"],
        cors_allow_credentials=False,
    )
    assert _values(final, b"access-control-allow-origin") == []
    assert _values(final, b"access-control-allow-credentials") == []


# --- 3. Perimeter (IP-block) responses carry security headers --------------


@pytest.mark.asyncio
async def test_ip_block_403_carries_security_headers(monkeypatch):
    import src.api as api

    class _Auth:
        def is_ip_blocked(self, ip):
            return True

    monkeypatch.setattr(api, "get_auth", lambda: _Auth())
    monkeypatch.setattr(
        "src.api.middleware.ip_blocking.cached_client_ip",
        lambda *a, **k: "203.0.113.5",
    )

    scope = {
        "type": "http",
        "path": "/api/v1/users",
        "method": "GET",
        "headers": [],
        "client": ("203.0.113.5", 1234),
        "query_string": b"",
        "scheme": "http",
        "server": ("testserver", 80),
    }
    messages: List[Dict[str, Any]] = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    async def downstream(scope, receive, send):  # pragma: no cover - never called
        raise AssertionError("blocked request reached the app")

    await IPBlockingMiddleware(downstream)(scope, receive, send)
    start = next(m for m in messages if m["type"] == "http.response.start")
    assert start["status"] == 403
    final = _headers(start)
    assert _values(final, b"x-content-type-options") == [b"nosniff"]
    assert _values(final, b"x-frame-options") == [b"SAMEORIGIN"]
    assert _values(final, b"content-security-policy")
    assert _values(final, b"strict-transport-security")
