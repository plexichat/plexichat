"""
Tests for request counting and limit enforcement.
"""

import pytest
import time

from src.core.ratelimit.models import BucketType, RateLimitConfig, RateLimitAlgorithm
from src.core.ratelimit.manager import RateLimitManager
from src.core import ratelimit


class TestBasicLimitEnforcement:
    """Tests for basic rate limit enforcement."""

    def test_allows_requests_under_limit(self, rate_limit_manager, test_user_id):
        """Test requests under limit are allowed."""
        for i in range(5):
            result = rate_limit_manager.check_rate_limit(
                user_id=test_user_id,
                route="GET /test",
            )
            assert result.allowed, f"Request {i+1} should be allowed"

    def test_blocks_requests_over_limit(self, memory_storage, test_user_id):
        """Test requests over limit are blocked."""
        config = RateLimitConfig(
            requests=3,
            window_seconds=60.0,
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
            assert result.allowed, f"Request {i+1} should be allowed"
        result = manager.check_rate_limit(
            user_id=test_user_id,
            route="GET /test",
        )
        assert not result.allowed, "Request 4 should be blocked"

    def test_remaining_count_decreases(self, memory_storage, test_user_id):
        """Test remaining count decreases with each request."""
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
        previous_remaining = None
        for i in range(5):
            result = manager.check_rate_limit(
                user_id=test_user_id,
                route="GET /test",
            )
            if previous_remaining is not None:
                assert result.remaining < previous_remaining
            previous_remaining = result.remaining

    def test_cost_parameter(self, memory_storage, test_user_id):
        """Test cost parameter consumes multiple tokens."""
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


class TestGlobalLimit:
    """Tests for global rate limiting."""

    def test_global_limit_enforced(self, memory_storage, test_user_id):
        """Test global limit is enforced."""
        global_config = RateLimitConfig(
            requests=5,
            window_seconds=1.0,
            burst=0,
            algorithm=RateLimitAlgorithm.FIXED_WINDOW,
        )
        manager = RateLimitManager(
            storage_backend=memory_storage,
            global_config=global_config,
            enable_global_limit=True,
        )
        for i in range(5):
            result = manager.check_rate_limit(user_id=test_user_id)
            assert result.allowed
        result = manager.check_rate_limit(user_id=test_user_id)
        assert not result.allowed
        assert result.is_global

    def test_global_limit_can_be_disabled(self, memory_storage, test_user_id):
        """Test global limit can be disabled."""
        manager = RateLimitManager(
            storage_backend=memory_storage,
            enable_global_limit=False,
        )
        for i in range(100):
            result = manager.check_rate_limit(user_id=test_user_id)
            assert result.allowed


class TestUserLimit:
    """Tests for per-user rate limiting."""

    def test_user_limit_enforced(self, memory_storage, test_user_id):
        """Test per-user limit is enforced."""
        user_config = RateLimitConfig(
            requests=5,
            window_seconds=60.0,
            burst=0,
            algorithm=RateLimitAlgorithm.FIXED_WINDOW,
        )
        manager = RateLimitManager(
            storage_backend=memory_storage,
            user_config=user_config,
            enable_global_limit=False,
        )
        for i in range(5):
            result = manager.check_rate_limit(user_id=test_user_id)
            assert result.allowed
        result = manager.check_rate_limit(user_id=test_user_id)
        assert not result.allowed

    def test_different_users_have_separate_limits(self, memory_storage):
        """Test different users have separate buckets."""
        user_config = RateLimitConfig(
            requests=3,
            window_seconds=60.0,
            burst=0,
            algorithm=RateLimitAlgorithm.FIXED_WINDOW,
        )
        manager = RateLimitManager(
            storage_backend=memory_storage,
            user_config=user_config,
            enable_global_limit=False,
        )
        for i in range(3):
            result = manager.check_rate_limit(user_id=111)
            assert result.allowed
        result = manager.check_rate_limit(user_id=111)
        assert not result.allowed
        for i in range(3):
            result = manager.check_rate_limit(user_id=222)
            assert result.allowed


class TestRouteLimit:
    """Tests for per-route rate limiting."""

    def test_route_limit_enforced(self, memory_storage, test_user_id):
        """Test per-route limit is enforced."""
        route_config = RateLimitConfig(
            requests=3,
            window_seconds=60.0,
            burst=0,
            algorithm=RateLimitAlgorithm.FIXED_WINDOW,
        )
        manager = RateLimitManager(
            storage_backend=memory_storage,
            route_configs={"POST /messages": route_config},
            enable_global_limit=False,
        )
        for i in range(3):
            result = manager.check_rate_limit(
                user_id=test_user_id,
                route="POST /messages",
            )
            assert result.allowed
        result = manager.check_rate_limit(
            user_id=test_user_id,
            route="POST /messages",
        )
        assert not result.allowed

    def test_different_routes_have_separate_limits(self, memory_storage, test_user_id):
        """Test different routes have separate buckets."""
        route_config = RateLimitConfig(
            requests=3,
            window_seconds=60.0,
            burst=0,
            algorithm=RateLimitAlgorithm.FIXED_WINDOW,
        )
        manager = RateLimitManager(
            storage_backend=memory_storage,
            route_configs={
                "POST /messages": route_config,
                "GET /messages": route_config,
            },
            enable_global_limit=False,
        )
        for i in range(3):
            result = manager.check_rate_limit(
                user_id=test_user_id,
                route="POST /messages",
            )
            assert result.allowed
        result = manager.check_rate_limit(
            user_id=test_user_id,
            route="POST /messages",
        )
        assert not result.allowed
        for i in range(3):
            result = manager.check_rate_limit(
                user_id=test_user_id,
                route="GET /messages",
            )
            assert result.allowed


class TestResourceLimit:
    """Tests for per-resource rate limiting."""

    def test_resource_limit_enforced(self, memory_storage, test_user_id, test_channel_id):
        """Test per-resource limit is enforced."""
        route_config = RateLimitConfig(
            requests=3,
            window_seconds=60.0,
            burst=0,
            algorithm=RateLimitAlgorithm.FIXED_WINDOW,
            scope=BucketType.RESOURCE,
        )
        manager = RateLimitManager(
            storage_backend=memory_storage,
            route_configs={"POST /channels/{id}/messages": route_config},
            enable_global_limit=False,
        )
        for i in range(3):
            result = manager.check_rate_limit(
                user_id=test_user_id,
                route="POST /channels/{id}/messages",
                resource_id=test_channel_id,
            )
            assert result.allowed
        result = manager.check_rate_limit(
            user_id=test_user_id,
            route="POST /channels/{id}/messages",
            resource_id=test_channel_id,
        )
        assert not result.allowed

    def test_different_resources_have_separate_limits(self, memory_storage, test_user_id):
        """Test different resources have separate buckets."""
        route_config = RateLimitConfig(
            requests=3,
            window_seconds=60.0,
            burst=0,
            algorithm=RateLimitAlgorithm.FIXED_WINDOW,
            scope=BucketType.RESOURCE,
        )
        manager = RateLimitManager(
            storage_backend=memory_storage,
            route_configs={"POST /channels/{id}/messages": route_config},
            enable_global_limit=False,
        )
        for i in range(3):
            result = manager.check_rate_limit(
                user_id=test_user_id,
                route="POST /channels/{id}/messages",
                resource_id=111,
            )
            assert result.allowed
        result = manager.check_rate_limit(
            user_id=test_user_id,
            route="POST /channels/{id}/messages",
            resource_id=111,
        )
        assert not result.allowed
        for i in range(3):
            result = manager.check_rate_limit(
                user_id=test_user_id,
                route="POST /channels/{id}/messages",
                resource_id=222,
            )
            assert result.allowed


class TestHourlyLimit:
    """Tests for hourly rate limiting."""

    def test_hourly_limit_tracked(self, memory_storage, test_user_id):
        """Test hourly limit is tracked."""
        config = RateLimitConfig(
            requests=100,
            window_seconds=1.0,
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
            assert result.allowed, f"Request {i+1} should be allowed"
        result = manager.check_rate_limit(
            user_id=test_user_id,
            route="GET /test",
        )
        assert not result.allowed


class TestDailyLimit:
    """Tests for daily rate limiting."""

    def test_daily_limit_tracked(self, memory_storage, test_user_id):
        """Test daily limit is tracked."""
        config = RateLimitConfig(
            requests=100,
            window_seconds=1.0,
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
            assert result.allowed, f"Request {i+1} should be allowed"
        result = manager.check_rate_limit(
            user_id=test_user_id,
            route="GET /test",
        )
        assert not result.allowed


class TestModuleInterface:
    """Tests for the module-level interface."""

    def test_check_rate_limit_function(self, setup_ratelimit, test_user_id):
        """Test module-level check_rate_limit function."""
        result = ratelimit.check_rate_limit(user_id=test_user_id)
        assert result.allowed

    def test_reset_bucket(self, setup_ratelimit, test_user_id):
        """Test resetting a specific bucket."""
        result = ratelimit.check_rate_limit(user_id=test_user_id)
        bucket_key = result.bucket_key
        ratelimit.reset_bucket(bucket_key)
        info = ratelimit.get_bucket_info(bucket_key)
        assert info is None

    def test_reset_user(self, setup_ratelimit, test_user_id):
        """Test resetting all buckets for a user."""
        ratelimit.check_rate_limit(user_id=test_user_id, route="GET /test1")
        ratelimit.check_rate_limit(user_id=test_user_id, route="GET /test2")
        ratelimit.reset_user(test_user_id)

    def test_reset_all(self, setup_ratelimit, test_user_id):
        """Test resetting all buckets."""
        ratelimit.check_rate_limit(user_id=test_user_id)
        ratelimit.check_rate_limit(user_id=99999)
        ratelimit.reset_all()
