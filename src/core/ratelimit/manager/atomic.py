"""Atomic bucket evaluation (blocking and awaitable).

Both paths share the same algorithms and result construction; only the
storage round trip differs. Every read-modify-write runs inside the storage
backend's per-key lock (``update_bucket``), so concurrent requests cannot
lose counter updates.

SECURITY: any backend failure is converted into a fail-closed denial. A limiter
that cannot read or write counters must never answer "allowed" -- that would
turn a store outage into an unlimited request path. The only exception is an
explicit bypass decision, which never touches storage and is handled by the
manager before reaching this module.
"""

from typing import Any, Dict, Optional

from ..models import BucketType, RateLimitAlgorithm, RateLimitConfig, RateLimitResult
from ..storage import RateLimitStorage
from .algorithms import apply_legacy_check, apply_secondary_limits
from .results import build_result, create_store_unavailable_result


def _log_store_failure(exc: Exception) -> None:
    """Report the outage without letting logging break the denial path."""
    try:
        import utils.logger as logger

        logger.critical(
            "rate-limit store unavailable, failing closed (denying request): %s",
            exc,
        )
    except Exception:
        pass


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

        if config.hourly_limit or config.daily_limit:
            # Hot path: one locked, atomic server-side script performs the
            # token spend AND the hourly/daily quota update. Backends without
            # a fused primitive fall back to the old two-call sequence inside
            # RateLimitStorage.eval_token_bucket_with_secondary. Either way a
            # missing ``applied`` verdict fails closed.
            def apply_secondary(state: Dict[str, Any]) -> Optional[float]:
                return apply_secondary_limits(state, config, cost, unix_now)

            try:
                (
                    allowed,
                    remaining,
                    reset_after,
                    applied,
                ) = storage.eval_token_bucket_with_secondary(
                    key,
                    config.effective_limit,
                    refill_rate,
                    cost,
                    config.hourly_limit,
                    config.daily_limit,
                    unix_now,
                    config.effective_limit,
                    apply_secondary=apply_secondary,
                )
            except Exception as exc:
                _log_store_failure(exc)
                return create_store_unavailable_result(config, unix_now, key)

            if not applied:
                return create_store_unavailable_result(config, unix_now, key)
        else:
            try:
                allowed, remaining, reset_after = storage.eval_token_bucket(
                    key, config.effective_limit, refill_rate, cost
                )
            except Exception as exc:
                _log_store_failure(exc)
                return create_store_unavailable_result(config, unix_now, key)

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

    try:
        storage.update_bucket(
            key,
            apply_legacy,
            ttl=max(config.window_seconds * 2, 86400),
        )
    except Exception as exc:
        _log_store_failure(exc)
        return create_store_unavailable_result(config, unix_now, key)

    # A backend that silently skipped the mutation (e.g. an unreachable store
    # or an unacquirable lock) leaves no outcome. Missing outcome == deny.
    if "result" not in legacy_outcome:
        return create_store_unavailable_result(config, unix_now, key)
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

        if config.hourly_limit or config.daily_limit:
            # See check_atomic_sync: the fused script (or the composing
            # fallback) must prove the quota mutation ran, or the request is
            # denied. This replaces two locked round trips on the same bucket.
            def apply_secondary(state: Dict[str, Any]) -> Optional[float]:
                return apply_secondary_limits(state, config, cost, unix_now)

            try:
                (
                    allowed,
                    remaining,
                    reset_after,
                    applied,
                ) = await storage.aeval_token_bucket_with_secondary(
                    key,
                    config.effective_limit,
                    refill_rate,
                    cost,
                    config.hourly_limit,
                    config.daily_limit,
                    unix_now,
                    config.effective_limit,
                    apply_secondary=apply_secondary,
                )
            except Exception as exc:
                _log_store_failure(exc)
                return create_store_unavailable_result(config, unix_now, key)

            if not applied:
                return create_store_unavailable_result(config, unix_now, key)
        else:
            try:
                allowed, remaining, reset_after = await storage.aeval_token_bucket(
                    key, config.effective_limit, refill_rate, cost
                )
            except Exception as exc:
                _log_store_failure(exc)
                return create_store_unavailable_result(config, unix_now, key)

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

    try:
        await storage.aupdate_bucket(
            key,
            apply_legacy,
            ttl=max(config.window_seconds * 2, 86400),
        )
    except Exception as exc:
        _log_store_failure(exc)
        return create_store_unavailable_result(config, unix_now, key)

    if "result" not in legacy_outcome:
        return create_store_unavailable_result(config, unix_now, key)
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
