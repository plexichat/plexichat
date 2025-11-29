"""
Tests for per-route limit configuration.
"""

import pytest
import time

from src.core.ratelimit.models import RateLimitConfig, RateLimitAlgorithm, BucketType
from src.core.ratelimit.manager import RateLimitManager
from src.core.ratelimit.config import (
    DEFAULT_ROUTE_LIMITS,
    get_route_config,
    get_default_config,
    is_bot_higher_limit_route,
    merge_route_configs,
)
from src.core.ratelimit.storage import MemoryStorage


class TestDefaultRouteConfigs:
    """Tests for default route configurations."""

    def test_login_route_config(self):
        """Test login route has strict limits."""
        config = get_route_config("POST /auth/login")
        assert config is not None
        assert config.requests == 5
        assert config.window_seconds == 60.0
        assert config.algorithm == RateLimitAlgorithm.FIXED_WINDOW

    def test_register_route_config(self):
        """Test register route has strict limits."""
        config = get_route_config("POST /auth/register")
        assert config is not None
        assert config.requests == 3
        assert config.hourly_limit == 10
        assert config.daily_limit == 20

    def test_messages_route_config(self):
        """Test messages route config."""
        config = get_route_config("POST /channels/{id}/messages")
        assert config is not None
        assert config.requests == 5
        assert config.window_seconds == 5.0
        assert config.algorithm == RateLimitAlgorithm.TOKEN_BUCKET

    def test_user_update_route_config(self):
        """Test user update route has strict limits."""
        config = get_route_config("PATCH /users/@me")
        assert config is not None
        assert config.requests == 2
        assert config.window_seconds == 60.0

    def test_reactions_route_config(self):
        """Test reactions route has fast limits."""
        config = get_route_config("PUT /channels/{id}/messages/{msg_id}/reactions/{emoji}")
        assert config is not None
        assert config.requests == 1
        assert config.window_seconds == 0.25

    def test_webhook_execute_route_config(self):
        """Test webhook execute route config."""
        config = get_route_config("POST /webhooks/{id}/{token}")
        assert config is not None
        assert config.scope == BucketType.WEBHOOK

    def test_unknown_route_returns_none(self):
        """Test unknown route returns None."""
        config = get_route_config("GET /unknown/route")
        assert config is None

    def test_default_config(self):
        """Test default config for unspecified routes."""
        config = get_default_config()
        assert config is not None
        assert config.requests > 0
        assert config.window_seconds > 0


class TestBotHigherLimitRoutes:
    """Tests for bot higher limit routes."""

    def test_messages_route_is_bot_higher(self):
        """Test messages route has higher bot limits."""
        assert is_bot_higher_limit_route("POST /channels/{id}/messages")

    def test_get_messages_is_bot_higher(self):
        """Test get messages route has higher bot limits."""
        assert is_bot_higher_limit_route("GET /channels/{id}/messages")

    def test_reactions_is_bot_higher(self):
        """Test reactions route has higher bot limits."""
        assert is_bot_higher_limit_route("PUT /channels/{id}/messages/{msg_id}/reactions/{emoji}")

    def test_login_is_not_bot_higher(self):
        """Test login route does not have higher bot limits."""
        assert not is_bot_higher_limit_route("POST /auth/login")


class TestRouteConfigMerging:
    """Tests for route config merging."""

    def test_merge_adds_new_routes(self):
        """Test merging adds new routes."""
        base = {"GET /a": RateLimitConfig(requests=10, window_seconds=60)}
        overrides = {"GET /b": RateLimitConfig(requests=20, window_seconds=60)}
        merged = merge_route_configs(base, overrides)
        assert "GET /a" in merged
        assert "GET /b" in merged

    def test_merge_overrides_existing(self):
        """Test merging overrides existing routes."""
        base = {"GET /a": RateLimitConfig(requests=10, window_seconds=60)}
        overrides = {"GET /a": RateLimitConfig(requests=20, window_seconds=60)}
        merged = merge_route_configs(base, overrides)
        assert merged["GET /a"].requests == 20

    def test_merge_preserves_base(self):
        """Test merging preserves base routes not in overrides."""
        base = {
            "GET /a": RateLimitConfig(requests=10, window_seconds=60),
            "GET /b": RateLimitConfig(requests=15, window_seconds=60),
        }
        overrides = {"GET /a": RateLimitConfig(requests=20, window_seconds=60)}
        merged = merge_route_configs(base, overrides)
        assert merged["GET /b"].requests == 15


class TestCustomRouteConfigs:
    """Tests for custom route configurations."""

    def test_custom_route_config_applied(self, memory_storage, test_user_id):
        """Test custom route config is applied."""
        custom_config = RateLimitConfig(
            requests=2,
            window_seconds=60.0,
            burst=0,
            algorithm=RateLimitAlgorithm.FIXED_WINDOW,
        )
        manager = RateLimitManager(
            storage_backend=memory_storage,
            route_configs={"GET /custom": custom_config},
            enable_global_limit=False,
        )
        for i in range(2):
            result = manager.check_rate_limit(
                user_id=test_user_id,
                route="GET /custom",
            )
            assert result.allowed
        result = manager.check_rate_limit(
            user_id=test_user_id,
            route="GET /custom",
        )
        assert not result.allowed

    def test_custom_config_overrides_default(self, memory_storage, test_user_id):
        """Test custom config overrides default."""
        custom_config = RateLimitConfig(
            requests=1,
            window_seconds=60.0,
            burst=0,
            algorithm=RateLimitAlgorithm.FIXED_WINDOW,
        )
        manager = RateLimitManager(
            storage_backend=memory_storage,
            route_configs={"POST /auth/login": custom_config},
            enable_global_limit=False,
        )
        result = manager.check_rate_limit(
            user_id=test_user_id,
            route="POST /auth/login",
        )
        assert result.allowed
        result = manager.check_rate_limit(
            user_id=test_user_id,
            route="POST /auth/login",
        )
        assert not result.allowed


class TestRouteScopes:
    """Tests for route scope configurations."""

    def test_route_scope_user(self, memory_storage, test_user_id):
        """Test route with USER scope."""
        config = RateLimitConfig(
            requests=3,
            window_seconds=60.0,
            burst=0,
            algorithm=RateLimitAlgorithm.FIXED_WINDOW,
            scope=BucketType.USER,
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

    def test_route_scope_resource(self, memory_storage, test_user_id, test_channel_id):
        """Test route with RESOURCE scope."""
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
        result = manager.check_rate_limit(
            user_id=test_user_id,
            route="POST /channels/{id}/messages",
            resource_id=99999,
        )
        assert result.allowed

    def test_route_scope_webhook(self, memory_storage, test_webhook_id):
        """Test route with WEBHOOK scope."""
        config = RateLimitConfig(
            requests=3,
            window_seconds=60.0,
            burst=0,
            algorithm=RateLimitAlgorithm.FIXED_WINDOW,
            scope=BucketType.WEBHOOK,
        )
        manager = RateLimitManager(
            storage_backend=memory_storage,
            route_configs={"POST /webhooks/{id}/{token}": config},
            enable_global_limit=False,
        )
        for i in range(3):
            result = manager.check_rate_limit(
                route="POST /webhooks/{id}/{token}",
                webhook_id=test_webhook_id,
                is_webhook=True,
            )
            assert result.allowed
        result = manager.check_rate_limit(
            route="POST /webhooks/{id}/{token}",
            webhook_id=test_webhook_id,
            is_webhook=True,
        )
        assert not result.allowed


class TestBotMultiplier:
    """Tests for bot rate limit multiplier."""

    def test_bot_gets_higher_limits(self, memory_storage, test_user_id):
        """Test bots get higher limits on applicable routes."""
        config = RateLimitConfig(
            requests=10,
            window_seconds=60.0,
            burst=0,
            algorithm=RateLimitAlgorithm.FIXED_WINDOW,
        )
        manager = RateLimitManager(
            storage_backend=memory_storage,
            route_configs={"POST /channels/{id}/messages": config},
            bot_multiplier=1.5,
            enable_global_limit=False,
        )
        for i in range(15):
            result = manager.check_rate_limit(
                user_id=test_user_id,
                route="POST /channels/{id}/messages",
                is_bot=True,
            )
            assert result.allowed, f"Bot request {i+1} should be allowed"
        result = manager.check_rate_limit(
            user_id=test_user_id,
            route="POST /channels/{id}/messages",
            is_bot=True,
        )
        assert not result.allowed

    def test_user_gets_normal_limits(self, memory_storage, test_user_id):
        """Test users get normal limits."""
        config = RateLimitConfig(
            requests=10,
            window_seconds=60.0,
            burst=0,
            algorithm=RateLimitAlgorithm.FIXED_WINDOW,
        )
        manager = RateLimitManager(
            storage_backend=memory_storage,
            route_configs={"POST /channels/{id}/messages": config},
            bot_multiplier=1.5,
            enable_global_limit=False,
        )
        for i in range(10):
            result = manager.check_rate_limit(
                user_id=test_user_id,
                route="POST /channels/{id}/messages",
                is_bot=False,
            )
            assert result.allowed
        result = manager.check_rate_limit(
            user_id=test_user_id,
            route="POST /channels/{id}/messages",
            is_bot=False,
        )
        assert not result.allowed


class TestWebhookMultiplier:
    """Tests for webhook rate limit multiplier."""

    def test_webhook_multiplier_applied(self, memory_storage, test_webhook_id):
        """Test webhook multiplier is applied."""
        config = RateLimitConfig(
            requests=10,
            window_seconds=60.0,
            burst=0,
            algorithm=RateLimitAlgorithm.FIXED_WINDOW,
            scope=BucketType.WEBHOOK,
        )
        manager = RateLimitManager(
            storage_backend=memory_storage,
            route_configs={"POST /webhooks/{id}/{token}": config},
            webhook_multiplier=2.0,
            enable_global_limit=False,
        )
        for i in range(20):
            result = manager.check_rate_limit(
                route="POST /webhooks/{id}/{token}",
                webhook_id=test_webhook_id,
                is_webhook=True,
            )
            assert result.allowed, f"Webhook request {i+1} should be allowed"
        result = manager.check_rate_limit(
            route="POST /webhooks/{id}/{token}",
            webhook_id=test_webhook_id,
            is_webhook=True,
        )
        assert not result.allowed
