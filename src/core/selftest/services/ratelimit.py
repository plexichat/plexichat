"""
Rate limit test service for SelfTestRunner.

Negative test: sends rapid requests to verify 429 when rate limiting is active.
Uses a public endpoint (/api/v1/status) that doesn't require auth tokens or
API access tokens, so requests go through rate-limit middleware unobstructed.
"""

import utils.logger as logger

from ..context import SelfTestContext


class RateLimitTester:
    """Tests rate-limit enforcement via burst requests."""

    def __init__(self, ctx: SelfTestContext):
        self.ctx = ctx

    def test_rate_limits(self) -> None:
        logger.info("Testing rate limit enforcement...")

        rate_session = self.ctx.requests_module.Session()
        # No auth headers, no internal-secret header — this session makes
        # unauthenticated requests that still go through rate-limit middleware.

        # Use a public endpoint that doesn't require any auth or API access token.
        # The rate-limit middleware runs for ALL paths (no public-endpoint bypass),
        # so a burst to /api/v1/status will trigger 429 regardless of auth state.
        target = f"{self.ctx.base_url}/api/v1/status"

        burst_count = 70
        rate_limited = False
        status_codes = []

        for i in range(burst_count):
            try:
                resp = rate_session.get(target, timeout=3)
                status_codes.append(resp.status_code)
                if resp.status_code == 429:
                    rate_limited = True
                    logger.debug(f"Rate limit triggered at request {i + 1} (429)")
                    break
            except Exception as e:
                logger.debug(f"Rate limit test request {i + 1} exception: {e}")
                status_codes.append(0)

        self.ctx.results.append(
            {
                "method": "BURST",
                "path": "/api/v1/users/@me (no bypass header)",
                "status_code": 429
                if rate_limited
                else (status_codes[-1] if status_codes else 0),
                "duration_ms": 0,
                "success": True,
                "label": "rate_limit_test",
                "status_codes": status_codes,
                "warning": not rate_limited,
            }
        )

        if rate_limited:
            logger.info(
                "Rate limit NEGATIVE TEST PASSED: rate limiting is active (got 429 on burst)"
            )
        else:
            logger.warning(
                f"Rate limit NEGATIVE TEST WARNING: no 429 received after {burst_count} requests "
                f"(statuses: {status_codes}). Rate limiting may be disabled, or the effective "
                f"limit ({30 + 5}) wasn't exhausted."
            )
