"""Security-hardening tests for the split rate-limit manager.

These cover the failure modes that are easy to reintroduce when the giant
``manager.py`` monolith is refactored: negative request costs, invalid
configs, oversized keys, bypass-hook failures, and cross-user bucket
matches during reset.
"""

import pytest

from typing import Any

from src.core.ratelimit.manager import RateLimitManager
from src.core.ratelimit.manager import bypass as bypass_module
from src.core.ratelimit.models import (
    BucketType,
    RateLimitAlgorithm,
    RateLimitConfig,
)
from src.core.ratelimit.storage import MemoryStorage


def _fixed(requests: int = 2, window_seconds: float = 60.0) -> RateLimitConfig:
    return RateLimitConfig(
        requests=requests,
        window_seconds=window_seconds,
        burst=0,
        algorithm=RateLimitAlgorithm.FIXED_WINDOW,
    )


def _manager(
    storage: MemoryStorage,
    config: RateLimitConfig | None = None,
    **kwargs: Any,
) -> RateLimitManager:
    return RateLimitManager(
        storage_backend=storage,
        route_configs={"GET /test": config or _fixed()},
        enable_global_limit=False,
        **kwargs,
    )


class TestCostValidation:
    """Request cost must never refund or underflow quota."""

    def test_negative_cost_is_rejected(self, memory_storage):
        manager = _manager(memory_storage)

        with pytest.raises(ValueError, match="non-negative"):
            manager.check_rate_limit(user_id=1, route="GET /test", cost=-1)

    def test_non_integer_cost_is_rejected(self, memory_storage):
        manager = _manager(memory_storage)
        bad_cost: Any = 1.5

        with pytest.raises(TypeError, match="integer"):
            manager.check_rate_limit(user_id=1, route="GET /test", cost=bad_cost)
        with pytest.raises(TypeError, match="integer"):
            manager.check_rate_limit(user_id=1, route="GET /test", cost=True)

    def test_zero_cost_is_allowed_without_consuming_quota(self, memory_storage):
        manager = _manager(memory_storage, _fixed(requests=2))

        for _ in range(10):
            assert manager.check_rate_limit(
                user_id=1, route="GET /test", cost=0
            ).allowed

        assert manager.check_rate_limit(user_id=1, route="GET /test").allowed
        assert manager.check_rate_limit(user_id=1, route="GET /test").allowed
        assert not manager.check_rate_limit(user_id=1, route="GET /test").allowed

    @pytest.mark.asyncio
    async def test_async_negative_cost_is_rejected(self, memory_storage):
        manager = _manager(memory_storage)

        with pytest.raises(ValueError, match="non-negative"):
            await manager.acheck_rate_limit(user_id=1, route="GET /test", cost=-5)


class TestConfigValidation:
    """Invalid config values must fail at construction, not mid-request."""

    @pytest.mark.parametrize(
        "config",
        [
            RateLimitConfig(requests=-1, window_seconds=60.0),
            RateLimitConfig(requests=1, window_seconds=0.0),
            RateLimitConfig(requests=1, window_seconds=-1.0),
            RateLimitConfig(requests=1, window_seconds=60.0, burst=-1),
            RateLimitConfig(
                requests=1,
                window_seconds=60.0,
                hourly_limit=-1,
            ),
            RateLimitConfig(
                requests=1,
                window_seconds=60.0,
                daily_limit=-1,
            ),
        ],
    )
    def test_invalid_route_config_is_rejected(self, config):
        with pytest.raises(ValueError):
            RateLimitManager(
                storage_backend=MemoryStorage(),
                route_configs={"GET /bad": config},
                enable_global_limit=False,
            )

    def test_negative_multiplier_is_rejected(self):
        with pytest.raises(ValueError, match="bot_multiplier"):
            RateLimitManager(
                storage_backend=MemoryStorage(),
                bot_multiplier=-1.0,
                enable_global_limit=False,
            )
        with pytest.raises(ValueError, match="webhook_multiplier"):
            RateLimitManager(
                storage_backend=MemoryStorage(),
                webhook_multiplier=float("nan"),
                enable_global_limit=False,
            )

    @pytest.mark.asyncio
    async def test_override_config_is_validated(self, memory_storage):
        manager = _manager(memory_storage)

        with pytest.raises(ValueError, match="window_seconds"):
            await manager.acheck_rate_limit(
                route="GET /test",
                override_config=RateLimitConfig(requests=1, window_seconds=0.0),
            )


class TestBypassHardening:
    """A broken bypass decision must fail closed."""

    def test_raising_bypass_check_fails_closed(self, memory_storage):
        def broken_bypass(user_id, is_admin, is_internal):
            raise RuntimeError("bypass backend unavailable")

        manager = _manager(
            memory_storage,
            _fixed(requests=1),
            bypass_check=broken_bypass,
        )

        assert manager.check_rate_limit(user_id=1, route="GET /test").allowed
        assert not manager.check_rate_limit(user_id=1, route="GET /test").allowed

    def test_non_callable_bypass_check_is_rejected(self, memory_storage):
        bad_check: Any = 42
        with pytest.raises(TypeError, match="callable"):
            RateLimitManager(
                storage_backend=memory_storage,
                bypass_check=bad_check,
                enable_global_limit=False,
            )

        manager = _manager(memory_storage)
        with pytest.raises(TypeError, match="callable"):
            manager.set_bypass_check(bad_check)

    def test_unreadable_admin_config_fails_closed(self, memory_storage, monkeypatch):
        def broken_config():
            raise RuntimeError("config unavailable")

        monkeypatch.setattr(bypass_module, "should_bypass_admin", broken_config)
        manager = _manager(memory_storage, _fixed(requests=1))

        assert manager.check_rate_limit(
            user_id=1, route="GET /test", is_admin=True
        ).allowed
        assert not manager.check_rate_limit(
            user_id=1, route="GET /test", is_admin=True
        ).allowed


class TestKeyHardening:
    """Bucket keys must stay bounded and unambiguous."""

    def test_oversized_components_are_bounded_and_deterministic(self, memory_storage):
        manager = _manager(memory_storage)
        route = "GET /" + "a" * 10_000

        first = manager._generate_bucket_key(BucketType.ROUTE, user_id=1, route=route)
        second = manager._generate_bucket_key(BucketType.ROUTE, user_id=1, route=route)

        assert first == second
        assert len(first) <= manager.MAX_ROUTE_KEY_LENGTH + 32

    def test_oversized_ip_is_bounded(self, memory_storage):
        manager = _manager(memory_storage)
        key = manager._generate_bucket_key(BucketType.IP, ip_address="x" * 10_000)

        assert len(key) <= manager.MAX_ROUTE_KEY_LENGTH + 32

    def test_route_cannot_impersonate_user_segment(self, memory_storage):
        manager = _manager(memory_storage, _fixed(requests=100))

        attacker = manager.check_rate_limit(
            user_id=999,
            route="GET /x:u:123",
        )
        victim = manager.check_rate_limit(
            user_id=123,
            route="GET /safe",
        )

        assert attacker.bucket_key != victim.bucket_key

        manager.reset_user(123)

        assert manager.get_bucket_info(victim.bucket_key) is None
        assert manager.get_bucket_info(attacker.bucket_key) is not None

    def test_reset_user_uses_exact_segment_match(self, memory_storage):
        manager = _manager(memory_storage, _fixed(requests=100))

        user_one = manager.check_rate_limit(user_id=1, route="GET /test")
        user_ten = manager.check_rate_limit(user_id=10, route="GET /test")

        manager.reset_user(1)

        assert manager.get_bucket_info(user_one.bucket_key) is None
        assert manager.get_bucket_info(user_ten.bucket_key) is not None


class TestCounterHardening:
    """Custom counters must not be able to rewrite limiter state."""

    @pytest.mark.parametrize(
        "field",
        ["tokens", "window_start", "request_count", "timestamps", "key"],
    )
    def test_reserved_counter_fields_are_rejected(self, memory_storage, field):
        manager = _manager(memory_storage)

        with pytest.raises(ValueError, match="reserved"):
            manager.increment_custom_usage("counter:1", field, 1)

    def test_negative_or_non_integer_counters_are_rejected(self, memory_storage):
        manager = _manager(memory_storage)
        bad_amount: Any = 1.5

        with pytest.raises(ValueError, match="non-negative"):
            manager.increment_custom_usage("counter:1", "count", -1)
        with pytest.raises(TypeError, match="integer"):
            manager.increment_custom_usage("counter:1", "count", bad_amount)

    def test_valid_counter_still_increments(self, memory_storage):
        manager = _manager(memory_storage)

        assert manager.increment_custom_usage("counter:1", "count", 1, ttl=60) == 1
        assert manager.increment_custom_usage("counter:1", "count", 2, ttl=60) == 3


class TestRobustness:
    """Malformed storage state and oversized costs must not corrupt results."""

    def test_unknown_bucket_type_state_does_not_raise(self, memory_storage):
        manager = _manager(memory_storage)
        memory_storage.set_bucket(
            "poisoned",
            {
                "bucket_type": "not-a-real-type",
                "timestamps": "not-a-list",
            },
        )

        info = manager.get_bucket_info("poisoned")

        assert info is not None
        assert info.bucket_type == BucketType.ROUTE
        assert info.request_timestamps == []

    def test_cost_larger_than_limit_has_no_negative_remaining(self, memory_storage):
        manager = _manager(memory_storage, _fixed(requests=5))

        result = manager.check_rate_limit(user_id=1, route="GET /test", cost=100)

        assert not result.allowed
        assert result.remaining >= 0
        assert result.retry_after is not None
        assert result.retry_after >= 0

    def test_callers_config_object_is_snapshotted(self, memory_storage):
        config = _fixed(requests=2)
        route_configs = {"GET /test": config}
        manager = RateLimitManager(
            storage_backend=memory_storage,
            route_configs=route_configs,
            enable_global_limit=False,
        )

        # Mutating the caller's objects after construction must not weaken
        # the running limiter.
        config.requests = 10_000
        route_configs["GET /injected"] = _fixed(requests=10_000)

        assert manager.check_rate_limit(user_id=1, route="GET /test").allowed
        assert manager.check_rate_limit(user_id=1, route="GET /test").allowed
        assert not manager.check_rate_limit(user_id=1, route="GET /test").allowed
