"""Focused tests for self-test service behavior."""

from types import SimpleNamespace

import pytest


@pytest.mark.unit
class TestRateLimitSelfTest:
    """The self-test must not assume a fixed deployment rate limit."""

    def test_burst_uses_effective_limit_header(self):
        """The request count follows the advertised limit and stops at 429."""
        from src.core.selftest.services.ratelimit import RateLimitTester

        class FakeResponse:
            def __init__(self, status_code, headers=None):
                self.status_code = status_code
                self.headers = headers or {}

        responses = iter(
            [
                FakeResponse(200, {"X-RateLimit-Limit": "3"}),
                FakeResponse(200),
                FakeResponse(429),
            ]
        )

        class FakeSession:
            def __init__(self):
                self.calls = 0

            def get(self, _target, timeout):
                assert timeout == 3
                self.calls += 1
                return next(responses)

        session = FakeSession()
        ctx = SimpleNamespace(
            requests_module=SimpleNamespace(Session=lambda: session),
            make_session=lambda: session,
            base_url="http://127.0.0.1:8000",
            results=[],
        )

        RateLimitTester(ctx).test_rate_limits()

        assert session.calls == 3
        assert ctx.results[0]["warning"] is False
        assert ctx.results[0]["status_code"] == 429
        assert ctx.results[0]["status_codes"] == [200, 200, 429]
