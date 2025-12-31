"""
Rate limit configuration - Default configurations and route limits.

Configuration is loaded from config.yaml under 'rate_limiting' key.
Defaults are used if config is not available.
"""

from typing import Dict, Optional, Any
import utils.config as app_config
from .models import RateLimitConfig, BucketType, RateLimitAlgorithm





def _build_global_limit() -> RateLimitConfig:
    """Build global rate limit from config."""
    return RateLimitConfig(
        requests=app_config.get("rate_limiting.global.requests", 50),
        window_seconds=app_config.get("rate_limiting.global.window_seconds", 1.0),
        burst=app_config.get("rate_limiting.global.burst", 10),
        algorithm=RateLimitAlgorithm.TOKEN_BUCKET,
        scope=BucketType.GLOBAL,
        include_in_global=False,
    )


def _build_user_limit() -> RateLimitConfig:
    """Build user rate limit from config."""
    return RateLimitConfig(
        requests=app_config.get("rate_limiting.user.requests", 120),
        window_seconds=app_config.get("rate_limiting.user.window_seconds", 60.0),
        burst=app_config.get("rate_limiting.user.burst", 20),
        algorithm=RateLimitAlgorithm.SLIDING_WINDOW,
        scope=BucketType.USER,
        hourly_limit=app_config.get("rate_limiting.user.hourly_limit", 3600),
        daily_limit=app_config.get("rate_limiting.user.daily_limit", 50000),
    )


def _build_ip_limit() -> RateLimitConfig:
    """Build IP rate limit from config."""
    return RateLimitConfig(
        requests=app_config.get("rate_limiting.ip.requests", 60),
        window_seconds=app_config.get("rate_limiting.ip.window_seconds", 60.0),
        burst=app_config.get("rate_limiting.ip.burst", 10),
        algorithm=RateLimitAlgorithm.SLIDING_WINDOW,
        scope=BucketType.IP,
        hourly_limit=app_config.get("rate_limiting.ip.hourly_limit", 1800),
        daily_limit=app_config.get("rate_limiting.ip.daily_limit", 10000),
    )


def get_global_limit() -> RateLimitConfig:
    """Get global rate limit configuration."""
    return _build_global_limit()


def get_user_limit() -> RateLimitConfig:
    """Get user rate limit configuration."""
    return _build_user_limit()


def get_ip_limit() -> RateLimitConfig:
    """Get IP rate limit configuration."""
    return _build_ip_limit()


def get_bot_multiplier() -> float:
    """Get bot rate limit multiplier."""
    return app_config.get("rate_limiting.bot_multiplier", 1.5)


def get_webhook_multiplier() -> float:
    """Get webhook rate limit multiplier."""
    return app_config.get("rate_limiting.webhook_multiplier", 1.0)


def get_user_multiplier(user_id: int) -> float:
    """
    Get rate limit multiplier for a specific user based on their tier.
    
    Args:
        user_id: User ID to get multiplier for
        
    Returns:
        Multiplier value (e.g., 1.0 for standard, 2.0 for alpha)
    """
    try:
        from src.core import features
        if features.is_setup():
            return features.get_rate_limit_multiplier(user_id)
    except Exception:
        pass
    return 1.0


def _get_rate_limit_config() -> Dict[str, Any]:
    """Get the full rate limiting configuration section."""
    return app_config.get("rate_limiting", {})


def is_rate_limiting_enabled() -> bool:
    """Check if rate limiting is enabled."""
    return _get_rate_limit_config().get("enabled", True)


def should_bypass_admin() -> bool:
    """Check if admins should bypass rate limits."""
    return _get_rate_limit_config().get("admin_bypass", True)


def should_bypass_internal() -> bool:
    """Check if internal requests should bypass rate limits."""
    return _get_rate_limit_config().get("internal_bypass", True)


# Legacy compatibility - lazy-loaded to avoid import-time config access
_cached_global_limit = None
_cached_user_limit = None
_cached_ip_limit = None


def _get_default_global_limit():
    """Get cached global limit (lazy-loaded)."""
    global _cached_global_limit
    if _cached_global_limit is None:
        _cached_global_limit = _build_global_limit()
    return _cached_global_limit


def _get_default_user_limit():
    """Get cached user limit (lazy-loaded)."""
    global _cached_user_limit
    if _cached_user_limit is None:
        _cached_user_limit = _build_user_limit()
    return _cached_user_limit


def _get_default_ip_limit():
    """Get cached IP limit (lazy-loaded)."""
    global _cached_ip_limit
    if _cached_ip_limit is None:
        _cached_ip_limit = _build_ip_limit()
    return _cached_ip_limit


# Module-level properties for backward compatibility
# These are accessed via functions to enable lazy loading
class _LazyLimitProxy:
    """Proxy that lazily loads rate limit configs."""

    @property
    def DEFAULT_GLOBAL_LIMIT(self):
        return _get_default_global_limit()

    @property
    def DEFAULT_USER_LIMIT(self):
        return _get_default_user_limit()

    @property
    def DEFAULT_IP_LIMIT(self):
        return _get_default_ip_limit()


# For direct attribute access, use the getter functions
DEFAULT_GLOBAL_LIMIT = None  # Use get_global_limit() instead
DEFAULT_USER_LIMIT = None  # Use get_user_limit() instead
DEFAULT_IP_LIMIT = None  # Use get_ip_limit() instead


DEFAULT_ROUTE_LIMITS: Dict[str, RateLimitConfig] = {
    "POST /auth/login": RateLimitConfig(
        requests=5,
        window_seconds=60.0,
        burst=0,
        algorithm=RateLimitAlgorithm.FIXED_WINDOW,
        scope=BucketType.ROUTE,
        hourly_limit=20,
    ),
    "POST /auth/register": RateLimitConfig(
        requests=3,
        window_seconds=60.0,
        burst=0,
        algorithm=RateLimitAlgorithm.FIXED_WINDOW,
        scope=BucketType.ROUTE,
        hourly_limit=10,
        daily_limit=20,
    ),
    "POST /auth/2fa": RateLimitConfig(
        requests=5,
        window_seconds=60.0,
        burst=2,
        algorithm=RateLimitAlgorithm.SLIDING_WINDOW,
        scope=BucketType.ROUTE,
    ),
    "POST /channels/{id}/messages": RateLimitConfig(
        requests=5,
        window_seconds=5.0,
        burst=3,
        algorithm=RateLimitAlgorithm.TOKEN_BUCKET,
        scope=BucketType.RESOURCE,
    ),
    "PATCH /channels/{id}/messages/{msg_id}": RateLimitConfig(
        requests=5,
        window_seconds=5.0,
        burst=2,
        algorithm=RateLimitAlgorithm.SLIDING_WINDOW,
        scope=BucketType.RESOURCE,
    ),
    "DELETE /channels/{id}/messages/{msg_id}": RateLimitConfig(
        requests=5,
        window_seconds=5.0,
        burst=2,
        algorithm=RateLimitAlgorithm.SLIDING_WINDOW,
        scope=BucketType.RESOURCE,
    ),
    "PUT /channels/{id}/messages/{msg_id}/reactions/{emoji}": RateLimitConfig(
        requests=1,
        window_seconds=0.25,
        burst=1,
        algorithm=RateLimitAlgorithm.TOKEN_BUCKET,
        scope=BucketType.RESOURCE,
    ),
    "DELETE /channels/{id}/messages/{msg_id}/reactions/{emoji}": RateLimitConfig(
        requests=1,
        window_seconds=0.25,
        burst=1,
        algorithm=RateLimitAlgorithm.TOKEN_BUCKET,
        scope=BucketType.RESOURCE,
    ),
    "PATCH /users/@me": RateLimitConfig(
        requests=2,
        window_seconds=60.0,
        burst=0,
        algorithm=RateLimitAlgorithm.FIXED_WINDOW,
        scope=BucketType.USER,
        hourly_limit=10,
    ),
    "POST /servers": RateLimitConfig(
        requests=10,
        window_seconds=60.0,
        burst=2,
        algorithm=RateLimitAlgorithm.SLIDING_WINDOW,
        scope=BucketType.USER,
        daily_limit=100,
    ),
    "DELETE /servers/{id}": RateLimitConfig(
        requests=1,
        window_seconds=60.0,
        burst=0,
        algorithm=RateLimitAlgorithm.FIXED_WINDOW,
        scope=BucketType.USER,
    ),
    "POST /relationships": RateLimitConfig(
        requests=5,
        window_seconds=60.0,
        burst=2,
        algorithm=RateLimitAlgorithm.SLIDING_WINDOW,
        scope=BucketType.USER,
        hourly_limit=50,
    ),
    "POST /relationships/block": RateLimitConfig(
        requests=10,
        window_seconds=60.0,
        burst=5,
        algorithm=RateLimitAlgorithm.SLIDING_WINDOW,
        scope=BucketType.USER,
    ),
    "POST /webhooks": RateLimitConfig(
        requests=5,
        window_seconds=60.0,
        burst=2,
        algorithm=RateLimitAlgorithm.SLIDING_WINDOW,
        scope=BucketType.USER,
    ),
    "POST /webhooks/{id}/{token}": RateLimitConfig(
        requests=5,
        window_seconds=2.0,
        burst=5,
        algorithm=RateLimitAlgorithm.TOKEN_BUCKET,
        scope=BucketType.WEBHOOK,
    ),
    "GET /channels/{id}/messages": RateLimitConfig(
        requests=10,
        window_seconds=10.0,
        burst=5,
        algorithm=RateLimitAlgorithm.SLIDING_WINDOW,
        scope=BucketType.RESOURCE,
    ),
    "GET /servers/{id}": RateLimitConfig(
        requests=20,
        window_seconds=10.0,
        burst=10,
        algorithm=RateLimitAlgorithm.SLIDING_WINDOW,
        scope=BucketType.ROUTE,
    ),
    "GET /users/@me": RateLimitConfig(
        requests=30,
        window_seconds=60.0,
        burst=10,
        algorithm=RateLimitAlgorithm.SLIDING_WINDOW,
        scope=BucketType.USER,
    ),
}


DEFAULT_WEBHOOK_CHANNEL_LIMIT = RateLimitConfig(
    requests=30,
    window_seconds=60.0,
    burst=10,
    algorithm=RateLimitAlgorithm.TOKEN_BUCKET,
    scope=BucketType.CHANNEL_WEBHOOK,
)


BOT_HIGHER_LIMIT_ROUTES = {
    "POST /channels/{id}/messages",
    "GET /channels/{id}/messages",
    "PUT /channels/{id}/messages/{msg_id}/reactions/{emoji}",
    "DELETE /channels/{id}/messages/{msg_id}/reactions/{emoji}",
}


def get_route_config(route: str) -> Optional[RateLimitConfig]:
    """
    Get rate limit configuration for a route.

    Args:
        route: Route pattern (e.g., "POST /channels/{id}/messages").

    Returns:
        RateLimitConfig or None if no specific config.
    """
    return DEFAULT_ROUTE_LIMITS.get(route)


def get_default_config() -> RateLimitConfig:
    """
    Get default rate limit configuration for unspecified routes.

    Returns:
        Default RateLimitConfig.
    """
    return RateLimitConfig(
        requests=30,
        window_seconds=30.0,
        burst=5,
        algorithm=RateLimitAlgorithm.SLIDING_WINDOW,
        scope=BucketType.ROUTE,
    )


def is_bot_higher_limit_route(route: str) -> bool:
    """Check if route has higher limits for bots."""
    return route in BOT_HIGHER_LIMIT_ROUTES


def merge_route_configs(
    base: Dict[str, RateLimitConfig],
    overrides: Dict[str, RateLimitConfig]
) -> Dict[str, RateLimitConfig]:
    """
    Merge route configurations with overrides.

    Args:
        base: Base configuration dictionary.
        overrides: Override configuration dictionary.

    Returns:
        Merged configuration dictionary.
    """
    result = dict(base)
    result.update(overrides)
    return result
