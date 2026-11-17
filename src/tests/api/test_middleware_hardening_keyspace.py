"""Regression tests for perimeter middleware hardening.

Covers:
- the auth-path rate-limit fallback must use the same bounded route key as
  the main limiter (no unbounded per-path bucket creation);
- perimeter-generated CORS preflight responses must carry the canonical
  security headers instead of bypassing the inner header layer;
- the rate limiter must still recognise a valid self-test request even when a
  stale cached false exists (no sibling of the cached_is_self_test poisoning).
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from starlette.requests import Request


def _request(path, method="POST", headers=None, client=("1.2.3.4", 1234)):
    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "headers": headers or [],
        "client": client,
        "query_string": b"",
        "scheme": "http",
        "server": ("testserver", 80),
        "state": {},
    }
    return Request(scope)


class TestAuthFallbackRateLimitKeyspace:
    """The fallback limiter must not re-open the F8 unbounded-keyspace hole."""

    @pytest.mark.asyncio
    async def test_fallback_uses_normalized_route_not_raw_path(self):
        from src.api.middleware.authentication import AuthenticationMiddleware
        from src.core.ratelimit.middleware import extract_route_info

        middleware = AuthenticationMiddleware(app=Mock())
        allowed = SimpleNamespace(allowed=True)
        check = AsyncMock(return_value=allowed)

        with (
            patch("src.core.ratelimit.is_setup", return_value=True),
            patch("src.core.ratelimit.acheck_rate_limit", check),
            patch(
                "src.api.middleware.authentication.cached_client_ip",
                return_value="1.2.3.4",
            ),
        ):
            result = await middleware._ip_fallback_rate_limit(
                _request("/api/v1/evil/99999")
            )

        assert result is None
        check.assert_awaited_once()
        kwargs = check.call_args.kwargs
        expected_route, expected_resource_id, expected_webhook_id = extract_route_info(
            "/api/v1/evil/99999", "POST"
        )
        assert kwargs["route"] == expected_route
        assert kwargs["resource_id"] == expected_resource_id
        assert kwargs["webhook_id"] == expected_webhook_id
        # The raw, attacker-controlled path must never reach the backend key.
        assert "/evil/99999" not in kwargs["route"]

    @pytest.mark.asyncio
    async def test_distinct_ids_share_one_bucket_key(self):
        from src.api.middleware.authentication import AuthenticationMiddleware

        middleware = AuthenticationMiddleware(app=Mock())
        check = AsyncMock(return_value=SimpleNamespace(allowed=True))
        seen = []

        async def _record(**kwargs):
            seen.append(kwargs["route"])
            return SimpleNamespace(allowed=True)

        check.side_effect = _record

        with (
            patch("src.core.ratelimit.is_setup", return_value=True),
            patch("src.core.ratelimit.acheck_rate_limit", check),
            patch(
                "src.api.middleware.authentication.cached_client_ip",
                return_value="1.2.3.4",
            ),
        ):
            await middleware._ip_fallback_rate_limit(_request("/api/v1/evil/111"))
            await middleware._ip_fallback_rate_limit(_request("/api/v1/evil/222"))

        assert len(seen) == 2
        assert seen[0] == seen[1], "numeric IDs must collapse to one bucket"


class TestPreflightSecurityHeaders:
    """Perimeter-generated preflight replies must not lose security headers."""

    def test_rejected_preflight_carries_security_headers(self, test_client):
        # OPTIONS without a preflight method falls through Starlette's CORS
        # middleware into the inline CORSPreflightMiddleware, which returns a
        # locally generated 400. That reply is produced outside the inner
        # SecurityHeadersMiddleware, so it must stamp its own headers.
        response = test_client.options("/api/v1/protected")
        assert response.status_code == 400
        headers = {k.lower(): v for k, v in response.headers.items()}
        assert headers.get("strict-transport-security", "").startswith("max-age=")
        assert headers.get("x-content-type-options") == "nosniff"
        assert "content-security-policy" in headers
        assert "x-frame-options" in headers

    def test_allowed_preflight_carries_security_headers(self, test_client):
        from src.api.config import get_api_config

        origin = next(iter(get_api_config().cors_origins), None)
        if not origin:
            pytest.skip("no CORS origins configured")
        response = test_client.options(
            "/api/v1/protected",
            headers={"Origin": origin},
        )
        assert response.status_code == 200
        headers = {k.lower(): v for k, v in response.headers.items()}
        assert headers.get("x-content-type-options") == "nosniff"
        assert "content-security-policy" in headers


class TestNoSelfTestSibling:
    """The self-test poisoning fix must also protect the limiter's getter."""

    def test_stale_cached_false_does_not_suppress_self_test_bypass(self):
        from src.api.middleware.rate_limiting import get_user_info_from_request
        from src.api.middleware.request_context import get_request_context

        request = _request(
            "/api/v1/whatever",
            headers=[(b"x-plexichat-internal-secret", b"correct-secret")],
            client=("127.0.0.1", 1234),
        )
        # Simulate an earlier evaluation that cached a negative verdict before
        # the context was fully bound. This is the exact sibling of the
        # cached_is_self_test poisoning bug.
        ctx = get_request_context(request)
        ctx.is_self_test = False

        with patch("src.api.is_self_test_request", return_value=True):
            info = get_user_info_from_request(request)

        assert info["is_internal"] is True
