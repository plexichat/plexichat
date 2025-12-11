"""
Tests for time window behavior and reset.
"""

import time

from src.core.ratelimit.models import RateLimitConfig, RateLimitAlgorithm
from src.core.ratelimit.manager import RateLimitManager


class TestFixedWindowReset:
    """Tests for fixed window reset behavior."""

    def test_window_resets_after_duration(self, memory_storage, test_user_id):
        """Test fixed window resets after window duration."""
        config = RateLimitConfig(
            requests=3,
            window_seconds=0.1,
            burst=0,
            algorithm=RateLimitAlgorithm.FIXED_WINDOW,
        )
        manager = RateLimitManager(
            storage_backend=memory_storage,
            route_configs={"GET /test": config},
            enable_global_limit=False,
        )
        for i in range(3):
            result = manager.check_rate_limit(
                user_id=test_user_id,
                route="GET /test",
            )
            assert result.allowed
        result = manager.check_rate_limit(
            user_id=test_user_id,
            route="GET /test",
        )
        assert not result.allowed
        time.sleep(0.15)
        result = manager.check_rate_limit(
            user_id=test_user_id,
            route="GET /test",
        )
        assert result.allowed

    def test_reset_after_header_accuracy(self, memory_storage, test_user_id):
        """Test reset_after header is accurate."""
        config = RateLimitConfig(
            requests=1,
            window_seconds=1.0,
            burst=0,
            algorithm=RateLimitAlgorithm.FIXED_WINDOW,
        )
        manager = RateLimitManager(
            storage_backend=memory_storage,
            route_configs={"GET /test": config},
            enable_global_limit=False,
        )
        result = manager.check_rate_limit(
            user_id=test_user_id,
            route="GET /test",
        )
        assert result.allowed
        result = manager.check_rate_limit(
            user_id=test_user_id,
            route="GET /test",
        )
        assert not result.allowed
        assert result.retry_after is not None
        assert 0 < result.retry_after <= 1.0


class TestSlidingWindowBehavior:
    """Tests for sliding window behavior."""

    def test_sliding_window_gradual_recovery(self, memory_storage, test_user_id):
        """Test sliding window allows gradual recovery."""
        config = RateLimitConfig(
            requests=3,
            window_seconds=0.3,
            burst=0,
            algorithm=RateLimitAlgorithm.SLIDING_WINDOW,
        )
        manager = RateLimitManager(
            storage_backend=memory_storage,
            route_configs={"GET /test": config},
            enable_global_limit=False,
        )
        for i in range(3):
            result = manager.check_rate_limit(
                user_id=test_user_id,
                route="GET /test",
            )
            assert result.allowed
            time.sleep(0.05)
        result = manager.check_rate_limit(
            user_id=test_user_id,
            route="GET /test",
        )
        assert not result.allowed
        time.sleep(0.2)
        result = manager.check_rate_limit(
            user_id=test_user_id,
            route="GET /test",
        )
        assert result.allowed

    def test_sliding_window_timestamp_cleanup(self, memory_storage, test_user_id):
        """Test sliding window cleans up old timestamps."""
        config = RateLimitConfig(
            requests=5,
            window_seconds=0.1,
            burst=0,
            algorithm=RateLimitAlgorithm.SLIDING_WINDOW,
        )
        manager = RateLimitManager(
            storage_backend=memory_storage,
            route_configs={"GET /test": config},
            enable_global_limit=False,
        )
        for i in range(5):
            manager.check_rate_limit(
                user_id=test_user_id,
                route="GET /test",
            )
        time.sleep(0.15)
        for i in range(5):
            result = manager.check_rate_limit(
                user_id=test_user_id,
                route="GET /test",
            )
            assert result.allowed


class TestTokenBucketRefill:
    """Tests for token bucket refill behavior."""

    def test_token_bucket_refills_over_time(self, memory_storage, test_user_id):
        """Test token bucket refills tokens over time."""
        config = RateLimitConfig(
            requests=10,
            window_seconds=1.0,
            burst=0,
            algorithm=RateLimitAlgorithm.TOKEN_BUCKET,
        )
        manager = RateLimitManager(
            storage_backend=memory_storage,
            route_configs={"GET /test": config},
            enable_global_limit=False,
        )
        for i in range(10):
            result = manager.check_rate_limit(
                user_id=test_user_id,
                route="GET /test",
            )
            assert result.allowed
        result = manager.check_rate_limit(
            user_id=test_user_id,
            route="GET /test",
        )
        assert not result.allowed
        time.sleep(0.2)
        result = manager.check_rate_limit(
            user_id=test_user_id,
            route="GET /test",
        )
        assert result.allowed

    def test_token_bucket_burst_capacity(self, memory_storage, test_user_id):
        """Test token bucket allows burst up to capacity."""
        config = RateLimitConfig(
            requests=5,
            window_seconds=1.0,
            burst=5,
            algorithm=RateLimitAlgorithm.TOKEN_BUCKET,
        )
        manager = RateLimitManager(
            storage_backend=memory_storage,
            route_configs={"GET /test": config},
            enable_global_limit=False,
        )
        for i in range(10):
            result = manager.check_rate_limit(
                user_id=test_user_id,
                route="GET /test",
            )
            assert result.allowed, f"Request {i+1} should be allowed (burst)"
        result = manager.check_rate_limit(
            user_id=test_user_id,
            route="GET /test",
        )
        assert not result.allowed


class TestLeakyBucketBehavior:
    """Tests for leaky bucket behavior."""

    def test_leaky_bucket_drains_over_time(self, memory_storage, test_user_id):
        """Test leaky bucket drains water over time."""
        config = RateLimitConfig(
            requests=10,
            window_seconds=1.0,
            burst=0,
            algorithm=RateLimitAlgorithm.LEAKY_BUCKET,
        )
        manager = RateLimitManager(
            storage_backend=memory_storage,
            route_configs={"GET /test": config},
            enable_global_limit=False,
        )
        for i in range(10):
            result = manager.check_rate_limit(
                user_id=test_user_id,
                route="GET /test",
            )
            assert result.allowed
        result = manager.check_rate_limit(
            user_id=test_user_id,
            route="GET /test",
        )
        assert not result.allowed
        time.sleep(0.2)
        result = manager.check_rate_limit(
            user_id=test_user_id,
            route="GET /test",
        )
        assert result.allowed


class TestResetTimestamps:
    """Tests for reset timestamp accuracy."""

    def test_reset_timestamp_is_unix_time(self, memory_storage, test_user_id):
        """Test reset timestamp is Unix time."""
        config = RateLimitConfig(
            requests=1,
            window_seconds=60.0,
            burst=0,
            algorithm=RateLimitAlgorithm.FIXED_WINDOW,
        )
        manager = RateLimitManager(
            storage_backend=memory_storage,
            route_configs={"GET /test": config},
            enable_global_limit=False,
        )
        result = manager.check_rate_limit(
            user_id=test_user_id,
            route="GET /test",
        )
        now = time.time()
        assert result.reset_at > now
        assert result.reset_at < now + 120

    def test_reset_after_matches_reset_timestamp(self, memory_storage, test_user_id):
        """Test reset_after is consistent with reset timestamp."""
        config = RateLimitConfig(
            requests=1,
            window_seconds=60.0,
            burst=0,
            algorithm=RateLimitAlgorithm.FIXED_WINDOW,
        )
        manager = RateLimitManager(
            storage_backend=memory_storage,
            route_configs={"GET /test": config},
            enable_global_limit=False,
        )
        result = manager.check_rate_limit(
            user_id=test_user_id,
            route="GET /test",
        )
        now = time.time()
        expected_reset = now + result.headers.reset_after
        assert abs(result.reset_at - expected_reset) < 1.0


class TestHourlyWindowReset:
    """Tests for hourly window reset."""

    def test_hourly_window_tracked_separately(self, memory_storage, test_user_id):
        """Test hourly window is tracked separately from main window."""
        config = RateLimitConfig(
            requests=100,
            window_seconds=0.1,
            burst=0,
            algorithm=RateLimitAlgorithm.FIXED_WINDOW,
            hourly_limit=5,
        )
        manager = RateLimitManager(
            storage_backend=memory_storage,
            route_configs={"GET /test": config},
            enable_global_limit=False,
        )
        for i in range(5):
            result = manager.check_rate_limit(
                user_id=test_user_id,
                route="GET /test",
            )
            assert result.allowed
            time.sleep(0.15)
        result = manager.check_rate_limit(
            user_id=test_user_id,
            route="GET /test",
        )
        assert not result.allowed


class TestDailyWindowReset:
    """Tests for daily window reset."""

    def test_daily_window_tracked_separately(self, memory_storage, test_user_id):
        """Test daily window is tracked separately from main window."""
        config = RateLimitConfig(
            requests=100,
            window_seconds=0.1,
            burst=0,
            algorithm=RateLimitAlgorithm.FIXED_WINDOW,
            daily_limit=5,
        )
        manager = RateLimitManager(
            storage_backend=memory_storage,
            route_configs={"GET /test": config},
            enable_global_limit=False,
        )
        for i in range(5):
            result = manager.check_rate_limit(
                user_id=test_user_id,
                route="GET /test",
            )
            assert result.allowed
            time.sleep(0.15)
        result = manager.check_rate_limit(
            user_id=test_user_id,
            route="GET /test",
        )
        assert not result.allowed
