"""
Rate Limiting Module - Advanced rate limiting for PlexiChat API.

Setup once in main.py, use anywhere via import.

Usage:
    # In main.py (setup once)
    from src.core import ratelimit
    ratelimit.setup()

    # In middleware or routes
    from src.core import ratelimit
    result = ratelimit.check_rate_limit(
        user_id=user_id,
        route="POST /channels/{id}/messages",
        resource_id=channel_id
    )
"""

from typing import Optional, Dict, Any, Callable

from .models import (
    RateLimitBucket,
    RateLimitConfig,
    RateLimitResult,
    RateLimitHeaders,
    BucketType,
    RateLimitAlgorithm,
)
from .config import (
    DEFAULT_ROUTE_LIMITS,
    get_route_config,
    get_default_config,
    get_global_limit,
    get_user_limit,
    get_ip_limit,
)
from .manager import RateLimitManager
from .decorators import rate_limit, custom_rate_limit
from .middleware import RateLimitMiddlewareASGI

# Alias for backward compatibility with tests
RateLimitMiddleware = RateLimitMiddlewareASGI

__all__ = [
    "setup",
    "check_rate_limit",
    "get_headers",
    "reset_bucket",
    "reset_user",
    "reset_all",
    "get_bucket_info",
    "set_bypass_check",
    "get_manager",
    "is_setup",
    "RateLimitBucket",
    "RateLimitConfig",
    "RateLimitResult",
    "RateLimitHeaders",
    "BucketType",
    "RateLimitAlgorithm",
    "RateLimitMiddlewareASGI",
    "RateLimitMiddleware",  # Alias
    "rate_limit",
    "custom_rate_limit",
    "DEFAULT_ROUTE_LIMITS",
    "get_route_config",
    "get_default_config",
    "get_global_limit",
    "get_user_limit",
    "get_ip_limit",
]

_manager: Optional[RateLimitManager] = None
_setup_complete: bool = False


def setup(
    storage_backend: Optional[Any] = None,
    route_configs: Optional[Dict[str, RateLimitConfig]] = None,
    global_config: Optional[RateLimitConfig] = None,
    user_config: Optional[RateLimitConfig] = None,
    ip_config: Optional[RateLimitConfig] = None,
    bot_multiplier: float = 1.2,
    webhook_multiplier: float = 1.0,
    bypass_check: Optional[Callable[..., bool]] = None,
    enable_global_limit: bool = True,
) -> None:
    """
    Initialize the rate limiting module.

    Args:
        storage_backend: Storage backend instance (default: in-memory).
        route_configs: Custom route configurations (merged with defaults).
        global_config: Global rate limit configuration.
        user_config: Per-user rate limit configuration (authenticated).
        ip_config: Per-IP rate limit configuration (unauthenticated).
        bot_multiplier: Multiplier for bot rate limits (higher = more lenient).
        webhook_multiplier: Multiplier for webhook rate limits.
        bypass_check: Callable(user_id, is_admin, is_internal) -> bool for bypass logic.
        enable_global_limit: Whether to enforce global rate limits.
    """
    global _manager, _setup_complete

    _manager = RateLimitManager(
        storage_backend=storage_backend,
        route_configs=route_configs,
        global_config=global_config,
        user_config=user_config,
        ip_config=ip_config,
        bot_multiplier=bot_multiplier,
        webhook_multiplier=webhook_multiplier,
        bypass_check=bypass_check,
        enable_global_limit=enable_global_limit,
    )
    _setup_complete = True


def _ensure_setup() -> None:
    """Ensure module is set up before use."""
    if not _setup_complete:
        raise RuntimeError(
            "Rate limit module not initialized. Call ratelimit.setup() first."
        )


def get_manager() -> RateLimitManager:
    """Get the rate limit manager instance."""
    _ensure_setup()
    assert _manager is not None
    return _manager


def check_rate_limit(
    user_id: Optional[int] = None,
    ip_address: Optional[str] = None,
    route: Optional[str] = None,
    resource_id: Optional[int] = None,
    is_bot: bool = False,
    is_webhook: bool = False,
    is_admin: bool = False,
    is_internal: bool = False,
    webhook_id: Optional[int] = None,
    cost: int = 1,
) -> RateLimitResult:
    """
    Check if a request is rate limited.

    Args:
        user_id: User making the request.
        route: Route pattern (e.g., "POST /channels/{id}/messages").
        resource_id: Resource ID for per-resource limits (channel_id, server_id).
        is_bot: Whether the requester is a bot.
        is_webhook: Whether this is a webhook request.
        is_admin: Whether the user is an admin.
        is_internal: Whether this is an internal request.
        webhook_id: Webhook ID for webhook-specific limits.
        cost: Request cost (default 1, some operations cost more).

    Returns:
        RateLimitResult with allowed status and headers.
    """
    _ensure_setup()
    assert _manager is not None
    return _manager.check_rate_limit(
        user_id=user_id,
        ip_address=ip_address,
        route=route,
        resource_id=resource_id,
        is_bot=is_bot,
        is_webhook=is_webhook,
        is_admin=is_admin,
        is_internal=is_internal,
        webhook_id=webhook_id,
        cost=cost,
    )


def get_headers(result: RateLimitResult) -> Dict[str, str]:
    """
    Get HTTP headers from a rate limit result.

    Args:
        result: Rate limit check result.

    Returns:
        Dictionary of HTTP headers.
    """
    _ensure_setup()
    assert _manager is not None
    return _manager.get_headers(result)


def reset_bucket(bucket_key: str) -> None:
    """
    Reset a specific rate limit bucket.

    Args:
        bucket_key: The bucket identifier to reset.
    """
    _ensure_setup()
    assert _manager is not None
    _manager.reset_bucket(bucket_key)


def reset_user(user_id: int) -> None:
    """
    Reset all rate limit buckets for a user.

    Args:
        user_id: User ID to reset.
    """
    _ensure_setup()
    assert _manager is not None
    _manager.reset_user(user_id)


def reset_all() -> None:
    """Reset all rate limit buckets."""
    _ensure_setup()
    assert _manager is not None
    _manager.reset_all()


def get_bucket_info(bucket_key: str) -> Optional[RateLimitBucket]:
    """
    Get information about a specific bucket.

    Args:
        bucket_key: The bucket identifier.

    Returns:
        Bucket information or None if not found.
    """
    _ensure_setup()
    assert _manager is not None
    return _manager.get_bucket_info(bucket_key)


def set_bypass_check(bypass_check: Callable[..., bool]) -> None:
    """
    Set the bypass check function.

    Args:
        bypass_check: Callable(user_id, is_admin, is_internal) -> bool.
    """
    _ensure_setup()
    assert _manager is not None
    _manager.set_bypass_check(bypass_check)


def is_setup() -> bool:
    """Check if the rate limit module is initialized."""
    return _setup_complete
