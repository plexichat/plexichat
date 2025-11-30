"""
Rate limit configuration - Default configurations and route limits.
"""

from typing import Dict, Optional
from .models import RateLimitConfig, BucketType, RateLimitAlgorithm


DEFAULT_GLOBAL_LIMIT = RateLimitConfig(
    requests=50,
    window_seconds=1.0,
    burst=10,
    algorithm=RateLimitAlgorithm.TOKEN_BUCKET,
    scope=BucketType.GLOBAL,
    include_in_global=False,
)


DEFAULT_USER_LIMIT = RateLimitConfig(
    requests=120,
    window_seconds=60.0,
    burst=20,
    algorithm=RateLimitAlgorithm.SLIDING_WINDOW,
    scope=BucketType.USER,
    hourly_limit=3600,
    daily_limit=50000,
)


DEFAULT_IP_LIMIT = RateLimitConfig(
    requests=60,
    window_seconds=60.0,
    burst=10,
    algorithm=RateLimitAlgorithm.SLIDING_WINDOW,
    scope=BucketType.IP,
    hourly_limit=1800,
    daily_limit=10000,
)


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
