"""
Rate limit test service for SelfTestRunner.

Negative test: sends rapid requests to verify 429 when rate limiting is active.
Uses a public endpoint (/api/v1/status) that doesn't require auth tokens or
API access tokens, so requests go through rate-limit middleware unobstructed.
"""

import utils.logger as logger

from ..context import SelfTestContext


_MAX_RATE_LIMIT_TEST_REQUESTS = 10_000


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

        # Read the effective route limit from the first response instead of
        # assuming a deployment-specific default. The first response consumes
        # one token from the same IP/route buckets used by the remaining burst.
        rate_limited = False
        status_codes = []
        burst_count = 0
        try:
            first_response = rate_session.get(target, timeout=3)
            status_codes.append(first_response.status_code)
            if first_response.status_code == 429:
                rate_limited = True
            else:
                try:
                    effective_limit = int(
                        first_response.headers.get("X-RateLimit-Limit", "")
                    )
                except (TypeError, ValueError):
                    effective_limit = 0
                burst_count = min(
                    max(effective_limit + 1, 2), _MAX_RATE_LIMIT_TEST_REQUESTS
                )
        except Exception as e:
            logger.debug(f"Rate limit test initial request exception: {e}")
            status_codes.append(0)

        for request_number in range(len(status_codes) + 1, burst_count + 1):
            try:
                resp = rate_session.get(target, timeout=3)
                status_codes.append(resp.status_code)
                if resp.status_code == 429:
                    rate_limited = True
                    logger.debug(
                        "Rate limit triggered at request %s (429)", request_number
                    )
                    break
            except Exception as e:
                logger.debug(
                    "Rate limit test request %s exception: %s", request_number, e
                )
                status_codes.append(0)

        self.ctx.results.append(
            {
                "method": "BURST",
                "path": "/api/v1/status (no bypass header)",
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
                f"(statuses: {status_codes}). Rate limiting may be disabled, the limit "
                "header was unavailable, or the safety cap was reached."
            )
