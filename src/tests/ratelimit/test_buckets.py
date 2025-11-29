"""
Tests for rate limit bucket creation and identification.
"""

import pytest
import time

from src.core.ratelimit.models import (
    RateLimitBucket,
    RateLimitConfig,
    BucketType,
    RateLimitAlgorithm,
)
from src.core.ratelimit.manager import RateLimitManager


class TestBucketCreation:
    """Tests for bucket creation."""

    def test_create_bucket_with_defaults(self, default_config):
        """Test creating a bucket with default values."""
        bucket = RateLimitBucket(
            key="test:bucket",
            bucket_type=BucketType.USER,
            config=default_config,
        )
        assert bucket.key == "test:bucket"
        assert bucket.bucket_type == BucketType.USER
        assert bucket.tokens == default_config.effective_limit
        assert bucket.request_count == 0

    def test_bucket_types(self, default_config):
        """Test all bucket types can be created."""
        for bucket_type in BucketType:
            bucket = RateLimitBucket(
                key=f"test:{bucket_type.value}",
                bucket_type=bucket_type,
                config=default_config,
            )
            assert bucket.bucket_type == bucket_type

    def test_bucket_with_custom_tokens(self, default_config):
        """Test creating bucket with custom token count."""
        bucket = RateLimitBucket(
            key="test:custom",
            bucket_type=BucketType.USER,
            config=default_config,
            tokens=5.0,
        )
        assert bucket.tokens == 5.0

    def test_bucket_timestamps_initialized(self, default_config):
        """Test bucket timestamps are initialized."""
        bucket = RateLimitBucket(
            key="test:timestamps",
            bucket_type=BucketType.USER,
            config=default_config,
        )
        assert bucket.last_update > 0
        assert bucket.window_start > 0
        assert isinstance(bucket.request_timestamps, list)


class TestBucketKeyGeneration:
    """Tests for bucket key generation."""

    def test_global_bucket_key(self, rate_limit_manager, test_user_id):
        """Test global bucket key generation."""
        key = rate_limit_manager._generate_bucket_key(
            BucketType.GLOBAL,
            user_id=test_user_id,
        )
        assert "global" in key
        assert str(test_user_id) in key

    def test_user_bucket_key(self, rate_limit_manager, test_user_id):
        """Test user bucket key generation."""
        key = rate_limit_manager._generate_bucket_key(
            BucketType.USER,
            user_id=test_user_id,
        )
        assert "user" in key
        assert str(test_user_id) in key

    def test_route_bucket_key(self, rate_limit_manager, test_user_id):
        """Test route bucket key generation."""
        key = rate_limit_manager._generate_bucket_key(
            BucketType.ROUTE,
            user_id=test_user_id,
            route="POST /channels/{id}/messages",
        )
        assert "route" in key
        assert str(test_user_id) in key

    def test_resource_bucket_key(self, rate_limit_manager, test_user_id, test_channel_id):
        """Test resource bucket key generation."""
        key = rate_limit_manager._generate_bucket_key(
            BucketType.RESOURCE,
            user_id=test_user_id,
            route="POST /channels/{id}/messages",
            resource_id=test_channel_id,
        )
        assert "resource" in key
        assert str(test_channel_id) in key

    def test_webhook_bucket_key(self, rate_limit_manager, test_webhook_id):
        """Test webhook bucket key generation."""
        key = rate_limit_manager._generate_bucket_key(
            BucketType.WEBHOOK,
            webhook_id=test_webhook_id,
            route="POST /webhooks/{id}/{token}",
        )
        assert "webhook" in key
        assert str(test_webhook_id) in key

    def test_unique_keys_for_different_users(self, rate_limit_manager):
        """Test different users get different bucket keys."""
        key1 = rate_limit_manager._generate_bucket_key(
            BucketType.USER,
            user_id=111,
        )
        key2 = rate_limit_manager._generate_bucket_key(
            BucketType.USER,
            user_id=222,
        )
        assert key1 != key2

    def test_unique_keys_for_different_routes(self, rate_limit_manager, test_user_id):
        """Test different routes get different bucket keys."""
        key1 = rate_limit_manager._generate_bucket_key(
            BucketType.ROUTE,
            user_id=test_user_id,
            route="POST /messages",
        )
        key2 = rate_limit_manager._generate_bucket_key(
            BucketType.ROUTE,
            user_id=test_user_id,
            route="GET /messages",
        )
        assert key1 != key2

    def test_unique_keys_for_different_resources(self, rate_limit_manager, test_user_id):
        """Test different resources get different bucket keys."""
        key1 = rate_limit_manager._generate_bucket_key(
            BucketType.RESOURCE,
            user_id=test_user_id,
            route="POST /messages",
            resource_id=111,
        )
        key2 = rate_limit_manager._generate_bucket_key(
            BucketType.RESOURCE,
            user_id=test_user_id,
            route="POST /messages",
            resource_id=222,
        )
        assert key1 != key2


class TestBucketIdGeneration:
    """Tests for bucket ID generation (for headers)."""

    def test_bucket_id_is_short(self, rate_limit_manager):
        """Test bucket ID is reasonably short."""
        key = "global:u:12345:r:abcdef12"
        bucket_id = rate_limit_manager._generate_bucket_id(key)
        assert len(bucket_id) == 16

    def test_bucket_id_is_consistent(self, rate_limit_manager):
        """Test same key produces same bucket ID."""
        key = "test:bucket:key"
        id1 = rate_limit_manager._generate_bucket_id(key)
        id2 = rate_limit_manager._generate_bucket_id(key)
        assert id1 == id2

    def test_bucket_id_is_unique(self, rate_limit_manager):
        """Test different keys produce different bucket IDs."""
        id1 = rate_limit_manager._generate_bucket_id("key1")
        id2 = rate_limit_manager._generate_bucket_id("key2")
        assert id1 != id2


class TestConfigEffectiveLimit:
    """Tests for config effective limit calculation."""

    def test_effective_limit_with_burst(self):
        """Test effective limit includes burst."""
        config = RateLimitConfig(
            requests=10,
            window_seconds=10.0,
            burst=5,
        )
        assert config.effective_limit == 15

    def test_effective_limit_no_burst(self):
        """Test effective limit without burst."""
        config = RateLimitConfig(
            requests=10,
            window_seconds=10.0,
            burst=0,
        )
        assert config.effective_limit == 10

    def test_effective_limit_burst_equals_requests(self):
        """Test effective limit when burst equals requests (default)."""
        config = RateLimitConfig(
            requests=10,
            window_seconds=10.0,
        )
        assert config.effective_limit == 10

    def test_config_with_multiplier(self):
        """Test config with multiplier applied."""
        config = RateLimitConfig(
            requests=10,
            window_seconds=10.0,
            burst=5,
            hourly_limit=100,
            daily_limit=1000,
        )
        new_config = config.with_multiplier(1.5)
        assert new_config.requests == 15
        assert new_config.burst == 7
        assert new_config.hourly_limit == 150
        assert new_config.daily_limit == 1500
        assert new_config.window_seconds == config.window_seconds
