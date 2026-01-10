"""
Rate limiting middleware integration for the API.
"""

from typing import Optional, Dict, Any, Callable, Type
import sys

from fastapi import Request

import utils.config as config
from src.core.ratelimit import RateLimitMiddlewareASGI as RateLimitMiddleware
from src.core.ratelimit.middleware import extract_route_info
from src.utils.net import get_client_ip


def extract_ip(request: Request) -> str:
    """Extract IP address using consolidated utility."""
    return get_client_ip(request)


def get_user_info_from_request(request: Request) -> Dict[str, Any]:
    """
    Extract user information from request for rate limiting.

    Args:
        request: FastAPI request object.

    Returns:
        Dictionary with user_id, is_bot, is_admin, is_internal, is_webhook, webhook_id.
    """
    user_info = {
        "user_id": None,
        "ip_address": extract_ip(request),
        "is_bot": False,
        "is_admin": False,
        "is_internal": False,
        "is_webhook": False,
        "webhook_id": None,
    }

    if hasattr(request.state, "user") and request.state.user:
        user = request.state.user
        user_info["user_id"] = getattr(user, "user_id", None) or getattr(
            user, "id", None
        )
        token_type = getattr(user, "token_type", "")
        user_info["is_bot"] = token_type == "bot"
        permissions = getattr(user, "permissions", {})
        if isinstance(permissions, dict):
            user_info["is_admin"] = (
                permissions.get("admin.*", False)
                or permissions.get("*", False)
            )

    # Secure bypass check
    bypass_secret = config.get("rate_limiting.bypass_secret")
    bypass_header = request.headers.get("X-RateLimit-Bypass")
    
    if bypass_secret and bypass_header == bypass_secret:
        user_info["is_internal"] = True
    elif getattr(request.state, "is_selftest", False):
        user_info["is_internal"] = True

    return user_info


def create_rate_limit_middleware(
    exclude_paths: Optional[list] = None,
    include_headers_on_success: bool = True,
    custom_user_info_getter: Optional[Callable] = None,
) -> Type[RateLimitMiddleware]:
    """
    Create a configured rate limit middleware instance.

    Args:
        exclude_paths: Additional paths to exclude from rate limiting.
        include_headers_on_success: Whether to include rate limit headers on success.
        custom_user_info_getter: Custom function to extract user info.

    Returns:
        Configured RateLimitMiddleware (requires app to be passed separately).
    """
    default_excludes = [
        "/",
        "/health",
        "/docs",
        "/redoc",
        "/openapi.json",
        # Removed /docs/api to allow centralized rate limiting for documentation
    ]
    all_excludes = list(set(default_excludes + (exclude_paths or [])))
    user_info_getter = custom_user_info_getter or get_user_info_from_request

    class ConfiguredRateLimitMiddleware(RateLimitMiddleware):
        def __init__(self, app):
            super().__init__(
                app=app,
                get_user_info=user_info_getter,
                exclude_paths=all_excludes,
                include_headers_on_success=include_headers_on_success,
            )

    return ConfiguredRateLimitMiddleware


__all__ = [
    "get_user_info_from_request",
    "create_rate_limit_middleware",
    "RateLimitMiddleware",
    "extract_route_info",
]
