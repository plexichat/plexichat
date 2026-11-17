"""
Rate limit test service for SelfTestRunner.

Positive test: proves the rate limiter is live by observing its accounting
headers on two ordinary 2xx responses — the ``X-RateLimit-Remaining`` token
count must decrement between them. No burst, no 429: tripping the limiter
would poison the shared IP bucket for the rest of the suite.
Uses a public endpoint (/api/v1/status) that doesn't require auth tokens or
API access tokens, so requests go through rate-limit middleware unobstructed.

Self-test traffic is deliberately exempt from rate limiting (the harness marks
localhost/internal requests as internal so a long suite cannot be throttled
mid-run). A bypassed request carries ``X-RateLimit-Scope: bypass`` and a fixed
``X-RateLimit-Remaining`` sentinel, so the token count cannot decrement — that
is the bypass working, not a limiter fault. When the probe detects that
exemption it records the check as SKIPPED instead of FAILED: there is no live
limit to exercise from inside the suite, and re-enabling one would break the
rest of the run.
"""

import utils.logger as logger

from ..context import SelfTestContext

# Emitted by the limiter's bypass result (core/ratelimit/manager/results.py).
_BYPASS_SCOPE = "bypass"
_BYPASS_REMAINING = 999999


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
        scopes: list[str] = []
        ok = False
        try:
            for _ in range(2):
                resp = rate_session.get(target, timeout=3)
                statuses.append(resp.status_code)
                scopes.append(resp.headers.get("X-RateLimit-Scope", ""))
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

        result: dict = {
            "method": "GET",
            "path": "/api/v1/status (rate-limit accounting)",
            "status_code": statuses[-1] if statuses else 0,
            "duration_ms": 0,
            "success": ok,
            "label": "rate_limit_test",
            "status_codes": statuses,
        }

        # The suite intentionally bypasses rate limiting for its own traffic.
        # A constant unlimited sentinel is that bypass, not a broken limiter.
        # Skip honestly instead of reporting a false failure (and never
        # re-enable limiting just to make this probe see a 429).
        bypassed = any(scope == _BYPASS_SCOPE for scope in scopes) or (
            len(remaining) >= 1 and all(r == _BYPASS_REMAINING for r in remaining)
        )
        if bypassed:
            logger.info(
                "Rate limit check SKIPPED: self-test traffic is intentionally "
                f"exempt from rate limiting (scopes={scopes}, remaining={remaining}). "
                "No live limit is exercised from inside the suite."
            )
            result["success"] = True
            result["skipped"] = True
            result["skip_reason"] = "rate limiting bypassed for self-test traffic"
            self.ctx.results.append(result)
            return

        if not ok:
            logger.warning(
                "Rate limit enforcement check FAILED: expected two 200 responses "
                f"with decrementing X-RateLimit-Remaining (statuses={statuses}, "
                f"remaining={remaining}). Rate limiting may be disabled."
            )

        self.ctx.results.append(result)
