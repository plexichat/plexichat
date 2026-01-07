"""
Tests for edge cases - concurrent requests, clock skew, etc.
"""

import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from src.core.ratelimit.models import RateLimitConfig, RateLimitAlgorithm, BucketType
from src.core.ratelimit.manager import RateLimitManager


class TestConcurrentRequests:
    """Tests for concurrent request handling."""

    def test_concurrent_requests_respect_limit(self, memory_storage, test_user_id):
        """Test concurrent requests respect rate limit."""
        config = RateLimitConfig(
            requests=10,
            window_seconds=60.0,
            burst=0,
            algorithm=RateLimitAlgorithm.FIXED_WINDOW,
        )
        manager = RateLimitManager(
            storage_backend=memory_storage,
            route_configs={"GET /test": config},
            enable_global_limit=False,
        )
        results = []

        def make_request():
            result = manager.check_rate_limit(
                user_id=test_user_id,
                route="GET /test",
            )
            return result.allowed

        with ThreadPoolExecutor(max_workers=20) as executor:
            futures = [executor.submit(make_request) for _ in range(20)]
            for future in as_completed(futures):
                results.append(future.result())
        allowed_count = sum(1 for r in results if r)
        assert allowed_count == 10

    def test_concurrent_requests_different_users(self, memory_storage):
        """Test concurrent requests from different users."""
        config = RateLimitConfig(
            requests=5,
            window_seconds=60.0,
            burst=0,
            algorithm=RateLimitAlgorithm.FIXED_WINDOW,
        )
        manager = RateLimitManager(
            storage_backend=memory_storage,
            route_configs={"GET /test": config},
            enable_global_limit=False,
        )
        results = {1: [], 2: [], 3: [], 4: []}

        def make_request(user_id):
            result = manager.check_rate_limit(
                user_id=user_id,
                route="GET /test",
            )
            return user_id, result.allowed

        with ThreadPoolExecutor(max_workers=20) as executor:
            futures = []
            for user_id in [1, 2, 3, 4]:
                for _ in range(10):
                    futures.append(executor.submit(make_request, user_id))
            for future in as_completed(futures):
                user_id, allowed = future.result()
                results[user_id].append(allowed)
        for user_id, user_results in results.items():
            allowed_count = sum(1 for r in user_results if r)
            assert allowed_count == 5, f"User {user_id} should have 5 allowed requests"

    def test_concurrent_requests_different_resources(
        self, memory_storage, test_user_id
    ):
        """Test concurrent requests to different resources."""
        config = RateLimitConfig(
            requests=3,
            window_seconds=60.0,
            burst=0,
            algorithm=RateLimitAlgorithm.FIXED_WINDOW,
            scope=BucketType.RESOURCE,
        )
        manager = RateLimitManager(
            storage_backend=memory_storage,
            route_configs={"POST /channels/{id}/messages": config},
            enable_global_limit=False,
        )
        results = {1: [], 2: [], 3: []}

        def make_request(resource_id):
            result = manager.check_rate_limit(
                user_id=test_user_id,
                route="POST /channels/{id}/messages",
                resource_id=resource_id,
            )
            return resource_id, result.allowed

        with ThreadPoolExecutor(max_workers=15) as executor:
            futures = []
            for resource_id in [1, 2, 3]:
                for _ in range(5):
                    futures.append(executor.submit(make_request, resource_id))
            for future in as_completed(futures):
                resource_id, allowed = future.result()
                results[resource_id].append(allowed)
        for resource_id, resource_results in results.items():
            allowed_count = sum(1 for r in resource_results if r)
            assert allowed_count == 3


class TestNullUserHandling:
    """Tests for handling null/anonymous users."""

    def test_null_user_id_allowed(self, memory_storage):
        """Test requests with null user ID are handled."""
        config = RateLimitConfig(
            requests=5,
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
            user_id=None,
            route="GET /test",
        )
        assert result.allowed

    def test_null_user_separate_from_authenticated(self, memory_storage, test_user_id):
        """Test null user has separate bucket from authenticated users."""
        config = RateLimitConfig(
            requests=2,
            window_seconds=60.0,
            burst=0,
            algorithm=RateLimitAlgorithm.FIXED_WINDOW,
        )
        manager = RateLimitManager(
            storage_backend=memory_storage,
            route_configs={"GET /test": config},
            enable_global_limit=False,
        )
        for i in range(2):
            result = manager.check_rate_limit(
                user_id=None,
                route="GET /test",
            )
            assert result.allowed
        result = manager.check_rate_limit(
            user_id=None,
            route="GET /test",
        )
        assert not result.allowed
        for i in range(2):
            result = manager.check_rate_limit(
                user_id=test_user_id,
                route="GET /test",
            )
            assert result.allowed


class TestZeroAndNegativeValues:
    """Tests for zero and negative value handling."""

    def test_zero_requests_config(self, memory_storage, test_user_id):
        """Test config with zero requests blocks all."""
        config = RateLimitConfig(
            requests=0,
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
        assert not result.allowed

    def test_zero_cost_request(self, memory_storage, test_user_id):
        """Test request with zero cost."""
        config = RateLimitConfig(
            requests=5,
            window_seconds=60.0,
            burst=0,
            algorithm=RateLimitAlgorithm.FIXED_WINDOW,
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
                cost=0,
            )
            assert result.allowed

    def test_high_cost_request(self, memory_storage, test_user_id):
        """Test request with high cost."""
        config = RateLimitConfig(
            requests=10,
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
            cost=5,
        )
        assert result.allowed
        assert result.remaining == 5
        result = manager.check_rate_limit(
            user_id=test_user_id,
            route="GET /test",
            cost=6,
        )
        assert not result.allowed


class TestVeryShortWindows:
    """Tests for very short time windows."""

    def test_sub_second_window(self, memory_storage, test_user_id):
        """Test sub-second time window."""
        config = RateLimitConfig(
            requests=2,
            window_seconds=0.1,
            burst=0,
            algorithm=RateLimitAlgorithm.FIXED_WINDOW,
        )
        manager = RateLimitManager(
            storage_backend=memory_storage,
            route_configs={"GET /test": config},
            enable_global_limit=False,
        )
        for i in range(2):
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

    def test_millisecond_window(self, memory_storage, test_user_id):
        """Test millisecond time window."""
        config = RateLimitConfig(
            requests=1,
            window_seconds=0.01,
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
        time.sleep(0.02)
        result = manager.check_rate_limit(
            user_id=test_user_id,
            route="GET /test",
        )
        assert result.allowed


class TestVeryLongWindows:
    """Tests for very long time windows."""

    def test_hourly_window(self, memory_storage, test_user_id):
        """Test hourly time window."""
        config = RateLimitConfig(
            requests=100,
            window_seconds=3600.0,
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
        assert result.headers.reset_after <= 3600

    def test_daily_window(self, memory_storage, test_user_id):
        """Test daily time window."""
        config = RateLimitConfig(
            requests=1000,
            window_seconds=86400.0,
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
        assert result.headers.reset_after <= 86400


class TestLargeUserIds:
    """Tests for large user IDs (snowflake IDs)."""

    def test_snowflake_user_id(self, memory_storage):
        """Test handling of snowflake-sized user IDs."""
        snowflake_id = 1234567890123456789
        config = RateLimitConfig(
            requests=5,
            window_seconds=60.0,
            burst=0,
            algorithm=RateLimitAlgorithm.FIXED_WINDOW,
        )
        manager = RateLimitManager(
            storage_backend=memory_storage,
            route_configs={"GET /test": config},
            enable_global_limit=False,
        )
        for i in range(5):
            result = manager.check_rate_limit(
                user_id=snowflake_id,
                route="GET /test",
            )
            assert result.allowed
        result = manager.check_rate_limit(
            user_id=snowflake_id,
            route="GET /test",
        )
        assert not result.allowed

    def test_different_snowflake_ids(self, memory_storage):
        """Test different snowflake IDs have separate buckets."""
        id1 = 1234567890123456789
        id2 = 9876543210987654321
        config = RateLimitConfig(
            requests=2,
            window_seconds=60.0,
            burst=0,
            algorithm=RateLimitAlgorithm.FIXED_WINDOW,
        )
        manager = RateLimitManager(
            storage_backend=memory_storage,
            route_configs={"GET /test": config},
            enable_global_limit=False,
        )
        for i in range(2):
            result = manager.check_rate_limit(user_id=id1, route="GET /test")
            assert result.allowed
        result = manager.check_rate_limit(user_id=id1, route="GET /test")
        assert not result.allowed
        for i in range(2):
            result = manager.check_rate_limit(user_id=id2, route="GET /test")
            assert result.allowed


class TestSpecialRoutePatterns:
    """Tests for special route patterns."""

    def test_route_with_special_characters(self, memory_storage, test_user_id):
        """Test route with special characters."""
        config = RateLimitConfig(
            requests=5,
            window_seconds=60.0,
            burst=0,
            algorithm=RateLimitAlgorithm.FIXED_WINDOW,
        )
        manager = RateLimitManager(
            storage_backend=memory_storage,
            route_configs={"GET /test?query=value&other=123": config},
            enable_global_limit=False,
        )
        result = manager.check_rate_limit(
            user_id=test_user_id,
            route="GET /test?query=value&other=123",
        )
        assert result.allowed

    def test_empty_route(self, memory_storage, test_user_id):
        """Test empty route string."""
        manager = RateLimitManager(
            storage_backend=memory_storage,
            enable_global_limit=False,
        )
        result = manager.check_rate_limit(
            user_id=test_user_id,
            route="",
        )
        assert result.allowed

    def test_none_route(self, memory_storage, test_user_id):
        """Test None route."""
        manager = RateLimitManager(
            storage_backend=memory_storage,
            enable_global_limit=False,
        )
        result = manager.check_rate_limit(
            user_id=test_user_id,
            route=None,
        )
        assert result.allowed


class TestResetOperations:
    """Tests for reset operations."""

    def test_reset_restores_limit(self, memory_storage, test_user_id):
        """Test resetting bucket restores limit."""
        config = RateLimitConfig(
            requests=2,
            window_seconds=60.0,
            burst=0,
            algorithm=RateLimitAlgorithm.FIXED_WINDOW,
        )
        manager = RateLimitManager(
            storage_backend=memory_storage,
            route_configs={"GET /test": config},
            enable_global_limit=False,
        )
        for i in range(2):
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
        manager.reset_bucket(result.bucket_key)
        for i in range(2):
            result = manager.check_rate_limit(
                user_id=test_user_id,
                route="GET /test",
            )
            assert result.allowed

    def test_reset_user_clears_all_buckets(self, memory_storage, test_user_id):
        """Test reset_user clears all user buckets."""
        config = RateLimitConfig(
            requests=1,
            window_seconds=60.0,
            burst=0,
            algorithm=RateLimitAlgorithm.FIXED_WINDOW,
        )
        manager = RateLimitManager(
            storage_backend=memory_storage,
            route_configs={
                "GET /test1": config,
                "GET /test2": config,
            },
            enable_global_limit=False,
        )
        manager.check_rate_limit(user_id=test_user_id, route="GET /test1")
        manager.check_rate_limit(user_id=test_user_id, route="GET /test2")
        result1 = manager.check_rate_limit(user_id=test_user_id, route="GET /test1")
        result2 = manager.check_rate_limit(user_id=test_user_id, route="GET /test2")
        assert not result1.allowed
        assert not result2.allowed
        manager.reset_user(test_user_id)
        result1 = manager.check_rate_limit(user_id=test_user_id, route="GET /test1")
        result2 = manager.check_rate_limit(user_id=test_user_id, route="GET /test2")
        assert result1.allowed
        assert result2.allowed

    def test_reset_all_clears_everything(self, memory_storage):
        """Test reset_all clears all buckets."""
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
        manager.check_rate_limit(user_id=111, route="GET /test")
        manager.check_rate_limit(user_id=222, route="GET /test")
        manager.check_rate_limit(user_id=333, route="GET /test")
        manager.reset_all()
        for user_id in [111, 222, 333]:
            result = manager.check_rate_limit(user_id=user_id, route="GET /test")
            assert result.allowed
