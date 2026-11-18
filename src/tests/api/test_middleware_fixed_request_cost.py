"""Regression: cut the *fixed* per-request middleware overhead without moving any gate.

The self-test's ~20 ms floor is dominated by inherent work (async stack,
token/DB verification, telemetry). Two costs sitting on top of that were avoidable:

* ``SecurityHeadersMiddleware`` read the API config twice per response
  (``_resolve_api_prefix`` + ``_cors_settings`` each called ``get_api_config``,
  and that snapshot stats the config file on every access).
* ``AuthenticationMiddleware._extract_token`` used ``re.fullmatch`` with a
  string pattern, rebuilding the cache key and calling through the module-level
  wrapper on every bearer token.

These tests pin three things: the observable output is byte-for-byte identical,
the redundant operations are gone, and every security check still fires.
"""

from typing import Any, Dict, List, Tuple

import pytest

from src.api.middleware import authentication as auth_module
from src.api.middleware import security as security_module
from src.api.middleware.response_policy import enforce_response_policy
from src.api.middleware.security import SecurityHeadersMiddleware, _security_policy

Header = Tuple[bytes, bytes]


def _scope(path: str = "/api/v1/users/@me") -> Dict[str, Any]:
    return {
        "type": "http",
        "path": path,
        "method": "GET",
        "headers": [],
        "query_string": b"",
        "client": ("127.0.0.1", 5555),
        "server": ("testserver", 80),
        "scheme": "http",
    }


async def _receive() -> Dict[str, Any]:
    return {"type": "http.request", "body": b"", "more_body": False}


async def _drive(response_headers: List[Header]) -> List[Header]:
    """Run SecurityHeadersMiddleware once and return the emitted headers."""

    async def downstream(scope, receive, send):
        await send(
            {"type": "http.response.start", "status": 200, "headers": response_headers}
        )
        await send({"type": "http.response.body", "body": b"ok"})

    sent: List[Dict[str, Any]] = []

    async def send(message):
        sent.append(message)

    await SecurityHeadersMiddleware(downstream)(_scope(), _receive, send)
    start = next(m for m in sent if m["type"] == "http.response.start")
    return list(start["headers"])


class TestSecurityHeadersSingleConfigRead:
    @pytest.mark.asyncio
    async def test_config_is_read_once_per_request(self, monkeypatch):
        from src.api import config as api_config

        calls: List[int] = []
        real = api_config.get_api_config

        def counting():
            calls.append(1)
            return real()

        monkeypatch.setattr(api_config, "get_api_config", counting)

        await _drive([(b"content-type", b"application/json")])

        # Before the fix this was TWO reads (two os.stat version checks).
        assert len(calls) == 1, f"expected one config read, got {len(calls)}"

    @pytest.mark.asyncio
    async def test_stamped_headers_match_direct_policy(self, monkeypatch):
        """Identical behavior: middleware output == direct enforce_response_policy."""
        from src.api.config import get_api_config

        settings = get_api_config()
        prefix = settings.api_prefix.rstrip("/") or "/api/v1"
        origins = tuple(settings.cors_origins)
        creds = bool(settings.cors_allow_credentials)

        upstream = [
            (b"content-type", b"application/json"),
            (b"X-Frame-Options", b"SAMEORIGIN"),
        ]
        stamped = await _drive(upstream)
        expected = enforce_response_policy(
            list(upstream),
            path="/api/v1/users/@me",
            api_prefix=prefix,
            cors_origins=origins,
            cors_allow_credentials=creds,
        )
        assert stamped == expected

    @pytest.mark.asyncio
    async def test_security_gates_still_run(self, monkeypatch):
        """Fewer reads must not weaken the CORS/header enforcement."""
        from src.api.config import get_api_config

        settings = get_api_config()
        monkeypatch.setattr(
            security_module,
            "_security_policy",
            lambda: (
                settings.api_prefix.rstrip("/") or "/api/v1",
                ("https://good.example",),
                True,
            ),
        )
        stamped = await _drive(
            [
                (b"X-Content-Type-Options", b"sniff"),
                (b"Content-Security-Policy", b"script-src *"),
                (b"Access-Control-Allow-Origin", b"https://evil.example"),
                (b"Access-Control-Allow-Credentials", b"true"),
            ]
        )
        values = {k.lower(): v for k, v in stamped}
        assert values[b"x-content-type-options"] == b"nosniff"
        assert b"script-src *" not in values[b"content-security-policy"]
        assert b"access-control-allow-origin" not in values
        assert b"access-control-allow-credentials" not in values


class TestSecurityPolicyFailClosed:
    def test_partial_config_keeps_independent_fallbacks(self, monkeypatch):
        from types import SimpleNamespace

        monkeypatch.setattr(
            "src.api.config.get_api_config",
            lambda: SimpleNamespace(api_prefix="/custom/api"),
        )
        assert _security_policy() == ("/custom/api", (), True)

    def test_missing_config_fails_closed(self, monkeypatch):
        def boom():
            raise RuntimeError("config unavailable")

        monkeypatch.setattr("src.api.config.get_api_config", boom)
        assert _security_policy() == ("/api/v1", (), True)


class TestTokenExtractionPatternCached:
    def test_extraction_results_are_identical(self):
        mw = auth_module.AuthenticationMiddleware(app=None)
        cases = {
            "Bearer abc123": "abc123",
            "Bot abc.def-_123": "abc.def-_123",
            "Bearer a.b_c-d": "a.b_c-d",
            "Basic abc": None,
            "bearer abc": None,
            "Bearer abc def": None,
            "Bearer abc!": None,
            "Bearer ": None,
            "": None,
            "Bearer abc,def": None,
        }
        for header, expected in cases.items():
            assert mw._extract_token(header) == expected, header

    def test_pattern_is_compiled_once(self, monkeypatch):
        auth_module._TOKEN_PATTERN_CACHE.clear()
        compile_calls: List[Any] = []
        real_compile = auth_module.re.compile

        def counting(pattern, *args, **kwargs):
            compile_calls.append(pattern)
            return real_compile(pattern, *args, **kwargs)

        monkeypatch.setattr(auth_module.re, "compile", counting)
        mw = auth_module.AuthenticationMiddleware(app=None)
        for _ in range(25):
            assert mw._extract_token("Bearer abc123") == "abc123"

        assert len(compile_calls) == 1
        assert len(auth_module._TOKEN_PATTERN_CACHE) == 1
