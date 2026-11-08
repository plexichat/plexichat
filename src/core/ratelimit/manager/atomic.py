"""Atomic bucket evaluation (blocking and awaitable).

Both paths share the same algorithms and result construction; only the
storage round trip differs. Every read-modify-write runs inside the storage
backend's per-key lock (``update_bucket``), so concurrent requests cannot
lose counter updates.
"""

from typing import Any, Dict, Optional

from ..models import BucketType, RateLimitAlgorithm, RateLimitConfig, RateLimitResult
from ..storage import RateLimitStorage
from .algorithms import apply_legacy_check, apply_secondary_limits
from .results import build_result


def check_atomic_sync(
    storage: RateLimitStorage,
    key: str,
    bucket_type: BucketType,
    config: RateLimitConfig,
    cost: int,
    unix_now: float,
) -> RateLimitResult:
    """Evaluate a bucket using atomic storage operations (blocking)."""
    if config.algorithm == RateLimitAlgorithm.TOKEN_BUCKET:
        refill_rate = config.requests / config.window_seconds
        allowed, remaining, reset_after = storage.eval_token_bucket(
            key, config.effective_limit, refill_rate, cost
        )

        if allowed and (config.hourly_limit or config.daily_limit):
            secondary_outcome: Dict[str, Optional[float]] = {}

            def apply_secondary(state: Dict[str, Any]) -> None:
                secondary_outcome["denied"] = apply_secondary_limits(
                    state, config, cost, unix_now
                )

            storage.update_bucket(key, apply_secondary, ttl=86400)

            denied_reset = secondary_outcome.get("denied")
            if denied_reset is not None:
                allowed = False
                reset_after = denied_reset

        return build_result(
            key,
            bucket_type,
            config,
            allowed,
            remaining,
            reset_after,
            unix_now,
            cost,
        )

    legacy_outcome: Dict[str, tuple[bool, int, float]] = {}

    def apply_legacy(state: Dict[str, Any]) -> None:
        legacy_outcome["result"] = apply_legacy_check(state, config, cost, unix_now)

    storage.update_bucket(
        key,
        apply_legacy,
        ttl=max(config.window_seconds * 2, 86400),
    )

    allowed, remaining, reset_after = legacy_outcome["result"]
    return build_result(
        key,
        bucket_type,
        config,
        allowed,
        remaining,
        reset_after,
        unix_now,
        cost,
    )


async def check_atomic_async(
    storage: RateLimitStorage,
    key: str,
    bucket_type: BucketType,
    config: RateLimitConfig,
    cost: int,
    unix_now: float,
) -> RateLimitResult:
    """Evaluate a bucket using atomic storage operations (awaitable)."""
    if config.algorithm == RateLimitAlgorithm.TOKEN_BUCKET:
        refill_rate = config.requests / config.window_seconds
        allowed, remaining, reset_after = await storage.aeval_token_bucket(
            key, config.effective_limit, refill_rate, cost
        )

        if allowed and (config.hourly_limit or config.daily_limit):
            secondary_outcome: Dict[str, Optional[float]] = {}

            def apply_secondary(state: Dict[str, Any]) -> None:
                secondary_outcome["denied"] = apply_secondary_limits(
                    state, config, cost, unix_now
                )

            await storage.aupdate_bucket(key, apply_secondary, ttl=86400)

            denied_reset = secondary_outcome.get("denied")
            if denied_reset is not None:
                allowed = False
                reset_after = denied_reset

        return build_result(
            key,
            bucket_type,
            config,
            allowed,
            remaining,
            reset_after,
            unix_now,
            cost,
        )

    legacy_outcome: Dict[str, tuple[bool, int, float]] = {}

    def apply_legacy(state: Dict[str, Any]) -> None:
        legacy_outcome["result"] = apply_legacy_check(state, config, cost, unix_now)

    await storage.aupdate_bucket(
        key,
        apply_legacy,
        ttl=max(config.window_seconds * 2, 86400),
    )

    allowed, remaining, reset_after = legacy_outcome["result"]
    return build_result(
        key,
        bucket_type,
        config,
        allowed,
        remaining,
        reset_after,
        unix_now,
        cost,
    )
