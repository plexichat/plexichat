"""
Route decorators for custom rate limits.
"""

import functools
from typing import Optional, Callable, Any

from fastapi import Request, HTTPException
from fastapi.responses import JSONResponse

from .models import RateLimitConfig, BucketType, RateLimitAlgorithm


_route_overrides: dict = {}


def rate_limit(
    requests: int,
    window_seconds: float,
    burst: int = 0,
    algorithm: RateLimitAlgorithm = RateLimitAlgorithm.SLIDING_WINDOW,
    scope: BucketType = BucketType.ROUTE,
    hourly_limit: Optional[int] = None,
    daily_limit: Optional[int] = None,
    per_resource: bool = False,
    resource_param: str = "id",
):
    """
    Decorator to apply custom rate limits to a route.

    Args:
        requests: Number of requests allowed per window.
        window_seconds: Window duration in seconds.
        burst: Additional burst allowance.
        algorithm: Rate limiting algorithm to use.
        scope: Bucket scope type.
        hourly_limit: Optional hourly limit.
        daily_limit: Optional daily limit.
        per_resource: Whether to apply per-resource limiting.
        resource_param: Path parameter name for resource ID.

    Usage:
        @router.post("/messages")
        @rate_limit(requests=5, window_seconds=5, burst=3)
        async def send_message(request: Request, ...):
            ...
    """
    config = RateLimitConfig(
        requests=requests,
        window_seconds=window_seconds,
        burst=burst,
        algorithm=algorithm,
        scope=BucketType.RESOURCE if per_resource else scope,
        hourly_limit=hourly_limit,
        daily_limit=daily_limit,
    )

    def decorator(func: Callable) -> Callable:
        route_key = f"{func.__module__}.{func.__name__}"
        _route_overrides[route_key] = {
            "config": config,
            "per_resource": per_resource,
            "resource_param": resource_param,
        }

        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            from src.core import ratelimit
            if not ratelimit.is_setup():
                return await func(*args, **kwargs)
            request = None
            for arg in args:
                if isinstance(arg, Request):
                    request = arg
                    break
            if request is None:
                request = kwargs.get("request")
            if request is None:
                return await func(*args, **kwargs)
            user_id = None
            is_bot = False
            is_admin = False
            if hasattr(request.state, "user") and request.state.user:
                user = request.state.user
                user_id = getattr(user, "user_id", None)
                is_bot = getattr(user, "token_type", "") == "bot"
                permissions = getattr(user, "permissions", {})
                is_admin = permissions.get("admin.*", False) or permissions.get("*", False)
            resource_id = None
            if per_resource:
                resource_id = kwargs.get(resource_param)
                if resource_id is not None:
                    try:
                        resource_id = int(resource_id)
                    except (ValueError, TypeError):
                        resource_id = None
            method = request.method
            path = request.url.path
            route = f"{method} {path}"
            manager = ratelimit.get_manager()
            original_config = manager._route_configs.get(route)
            manager._route_configs[route] = config
            try:
                result = ratelimit.check_rate_limit(
                    user_id=user_id,
                    route=route,
                    resource_id=resource_id,
                    is_bot=is_bot,
                    is_admin=is_admin,
                )
            finally:
                if original_config:
                    manager._route_configs[route] = original_config
                else:
                    manager._route_configs.pop(route, None)
            if not result.allowed:
                headers = ratelimit.get_headers(result)
                raise HTTPException(
                    status_code=429,
                    detail=result.response_body,
                    headers=headers,
                )
            return await func(*args, **kwargs)
        return wrapper
    return decorator


def custom_rate_limit(
    config: RateLimitConfig,
    get_bucket_key: Optional[Callable[[Request], str]] = None,
    get_resource_id: Optional[Callable[[Request, dict], Optional[int]]] = None,
):
    """
    Decorator for fully custom rate limiting logic.

    Args:
        config: Rate limit configuration.
        get_bucket_key: Custom function to generate bucket key.
        get_resource_id: Custom function to extract resource ID.

    Usage:
        @router.post("/custom")
        @custom_rate_limit(
            config=RateLimitConfig(requests=10, window_seconds=60),
            get_bucket_key=lambda req: f"custom:{req.client.host}",
        )
        async def custom_endpoint(request: Request):
            ...
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            from src.core import ratelimit
            if not ratelimit.is_setup():
                return await func(*args, **kwargs)
            request = None
            for arg in args:
                if isinstance(arg, Request):
                    request = arg
                    break
            if request is None:
                request = kwargs.get("request")
            if request is None:
                return await func(*args, **kwargs)
            user_id = None
            is_bot = False
            is_admin = False
            if hasattr(request.state, "user") and request.state.user:
                user = request.state.user
                user_id = getattr(user, "user_id", None)
                is_bot = getattr(user, "token_type", "") == "bot"
                permissions = getattr(user, "permissions", {})
                is_admin = permissions.get("admin.*", False) or permissions.get("*", False)
            resource_id = None
            if get_resource_id:
                resource_id = get_resource_id(request, kwargs)
            method = request.method
            path = request.url.path
            route = f"{method} {path}"
            if get_bucket_key:
                custom_key = get_bucket_key(request)
                route = f"custom:{custom_key}"
            manager = ratelimit.get_manager()
            manager._route_configs[route] = config
            try:
                result = ratelimit.check_rate_limit(
                    user_id=user_id,
                    route=route,
                    resource_id=resource_id,
                    is_bot=is_bot,
                    is_admin=is_admin,
                )
            finally:
                manager._route_configs.pop(route, None)
            if not result.allowed:
                headers = ratelimit.get_headers(result)
                raise HTTPException(
                    status_code=429,
                    detail=result.response_body,
                    headers=headers,
                )
            return await func(*args, **kwargs)
        return wrapper
    return decorator


def get_route_override(func: Callable) -> Optional[dict]:
    """Get rate limit override for a function if set."""
    route_key = f"{func.__module__}.{func.__name__}"
    return _route_overrides.get(route_key)


def clear_route_overrides() -> None:
    """Clear all route overrides."""
    _route_overrides.clear()
