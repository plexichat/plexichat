"""
Rate limit test service for SelfTestRunner.

Positive test: proves the rate limiter is live by observing its accounting
headers on two ordinary 2xx responses — the ``X-RateLimit-Remaining`` token
count must decrement between them. No burst, no 429: tripping the limiter
would poison the shared IP bucket for the rest of the suite.
Uses a public endpoint (/api/v1/status) that doesn't require auth tokens or
API access tokens, so requests go through rate-limit middleware unobstructed.
"""

import utils.logger as logger

from ..context import SelfTestContext


class RateLimitTester:
    """Proves rate-limit enforcement via accounting headers (2xx only)."""

    def __init__(self, ctx: SelfTestContext):
        self.ctx = ctx

    def test_rate_limits(self) -> None:
        logger.info("Testing rate limit enforcement...")

        rate_session = self.ctx.make_session()
        # No auth headers, no internal-secret header — this session makes
        # unauthenticated requests that still go through rate-limit middleware.

        # Use a public endpoint that doesn't require any auth or API access token.
        # The rate-limit middleware runs for ALL paths (no public-endpoint bypass),
        # so its accounting headers are present regardless of auth state.
        target = f"{self.ctx.base_url}/api/v1/status"

        remaining: list[int] = []
        statuses: list[int] = []
        ok = False
        try:
            for _ in range(2):
                resp = rate_session.get(target, timeout=3)
                statuses.append(resp.status_code)
                if resp.status_code != 200:
                    break
                try:
                    remaining.append(int(resp.headers.get("X-RateLimit-Remaining", "")))
                except (TypeError, ValueError):
                    break
            else:
                # Both responses 2xx with parseable counters: the second must
                # show fewer remaining tokens (the bucket spent tokens on our
                # requests). Strict decrease, not an exact delta: refills are
                # time-based and other suite traffic shares the IP bucket.
                ok = len(remaining) == 2 and remaining[1] < remaining[0]
                if ok:
                    logger.info(
                        "Rate limit enforcement PASSED: limiter accounting live "
                        f"(remaining {remaining[0]} -> {remaining[1]})"
                    )
        except Exception as e:
            logger.debug(f"Rate limit test request exception: {e}")
            statuses.append(0)

        if not ok:
            logger.warning(
                "Rate limit enforcement check FAILED: expected two 200 responses "
                f"with decrementing X-RateLimit-Remaining (statuses={statuses}, "
                f"remaining={remaining}). Rate limiting may be disabled."
            )

        self.ctx.results.append(
            {
                "method": "GET",
                "path": "/api/v1/status (rate-limit accounting)",
                "status_code": statuses[-1] if statuses else 0,
                "duration_ms": 0,
                "success": ok,
                "label": "rate_limit_test",
                "status_codes": statuses,
            }
        )
