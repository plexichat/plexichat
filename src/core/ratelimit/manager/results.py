"""Rate-limit result construction.

Header and result values are normalized here so a malformed configuration or
storage state can never emit ``NaN``/``inf`` headers or a negative remaining
count.
"""

import math
from typing import Any, Dict, Optional

from ..config import get_default_config
from ..models import (
    BucketType,
    RateLimitBucket,
    RateLimitConfig,
    RateLimitHeaders,
    RateLimitResult,
)
from .keys import generate_bucket_id

_BYPASS_LIMIT = 999999
_BYPASS_WINDOW_SECONDS = 60.0


def _finite_non_negative(value: Any, fallback: float) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return fallback
    if not math.isfinite(numeric) or numeric < 0:
        return fallback
    return numeric


def build_result(
    key: str,
    bucket_type: BucketType,
    config: RateLimitConfig,
    allowed: bool,
    remaining: int,
    reset_after: float,
    unix_now: float,
    cost: int,
) -> RateLimitResult:
    """Assemble a :class:`RateLimitResult` from a raw bucket outcome."""
    safe_now = _finite_non_negative(unix_now, 0.0)
    safe_reset_after = _finite_non_negative(reset_after, config.window_seconds)
    safe_remaining = max(0, int(remaining))
    retry_after = safe_reset_after if not allowed else None

    return RateLimitResult(
        allowed=allowed,
        headers=RateLimitHeaders(
            limit=config.effective_limit,
            remaining=safe_remaining,
            reset=safe_now + safe_reset_after,
            reset_after=safe_reset_after,
            bucket=generate_bucket_id(key),
            is_global=(bucket_type == BucketType.GLOBAL),
            retry_after=retry_after,
            scope=bucket_type.value,
        ),
        bucket_key=key,
        bucket_type=bucket_type,
        remaining=safe_remaining,
        reset_at=safe_now + safe_reset_after,
        retry_after=retry_after,
        is_global=(bucket_type == BucketType.GLOBAL),
        limited_by=bucket_type.value if not allowed else None,
        cost=cost,
    )


def create_bypass_result(route: Optional[str], unix_now: float) -> RateLimitResult:
    """Create a result for a bypassed request."""
    safe_now = _finite_non_negative(unix_now, 0.0)
    return RateLimitResult(
        allowed=True,
        headers=RateLimitHeaders(
            limit=_BYPASS_LIMIT,
            remaining=_BYPASS_LIMIT,
            reset=safe_now + _BYPASS_WINDOW_SECONDS,
            reset_after=_BYPASS_WINDOW_SECONDS,
            bucket="bypass",
            is_global=False,
            scope="bypass",
        ),
        bucket_key="bypass",
        bucket_type=BucketType.USER,
        remaining=_BYPASS_LIMIT,
        reset_at=safe_now + _BYPASS_WINDOW_SECONDS,
        cost=0,
    )


#: Retry window used when the limiter cannot reach its storage backend.
STORE_UNAVAILABLE_RETRY_AFTER = 60.0


def create_store_unavailable_result(
    config: RateLimitConfig,
    unix_now: float,
    bucket_key: str = "store-unavailable",
) -> RateLimitResult:
    """Build a FAIL-CLOSED denial for a backend outage.

    A rate limiter that cannot read or write its counters has no idea whether
    a request is over quota. Returning "allowed" in that state turns any
    transient store outage into an unbounded free-fire window, so we deny with
    a conservative retry hint and a complete set of rate-limit headers. The
    headers keep clients (and monitors) from interpreting the denial as an
    ordinary exhausted bucket.
    """
    safe_now = _finite_non_negative(unix_now, 0.0)
    retry_after = STORE_UNAVAILABLE_RETRY_AFTER
    headers = RateLimitHeaders(
        limit=config.effective_limit,
        remaining=0,
        reset=safe_now + retry_after,
        reset_after=retry_after,
        bucket="unavailable",
        is_global=False,
        retry_after=retry_after,
        scope="store",
    )
    return RateLimitResult(
        allowed=False,
        headers=headers,
        bucket_key=bucket_key,
        bucket_type=BucketType.GLOBAL,
        remaining=0,
        reset_at=safe_now + retry_after,
        retry_after=retry_after,
        is_global=False,
        limited_by="store",
        cost=0,
    )


def bucket_from_state(state: Dict[str, Any], bucket_key: str) -> RateLimitBucket:
    """Build a :class:`RateLimitBucket` from a raw state dict.

    Storage is treated as untrusted: an unknown ``bucket_type`` falls back to
    ``ROUTE`` instead of raising, and a non-list timestamp field is replaced
    with an empty list.
    """
    raw_bucket_type = state.get("bucket_type", BucketType.ROUTE.value)
    try:
        bucket_type = BucketType(raw_bucket_type)
    except (TypeError, ValueError):
        bucket_type = BucketType.ROUTE

    timestamps = state.get("timestamps", [])
    if not isinstance(timestamps, list):
        timestamps = []

    return RateLimitBucket(
        key=state.get("key", bucket_key),
        bucket_type=bucket_type,
        config=get_default_config(),
        tokens=state.get("tokens", 0.0),
        last_update=state.get("last_update", 0.0),
        window_start=state.get("window_start", 0.0),
        request_count=state.get("request_count", 0),
        hourly_count=state.get("hourly_count", 0),
        hourly_reset=state.get("hourly_reset", 0.0),
        daily_count=state.get("daily_count", 0),
        daily_reset=state.get("daily_reset", 0.0),
        request_timestamps=list(timestamps),
    )
