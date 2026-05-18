"""
Rate limiting middleware integration for the API.
"""

from typing import Optional, Dict, Any, Callable, Type

from fastapi import Request

import utils.config as config
from src.core.ratelimit import RateLimitMiddlewareASGI as RateLimitMiddleware
from src.core.ratelimit.middleware import extract_route_info
from src.utils.net import get_client_ip


import hmac


def extract_ip(request: Request) -> Optional[str]:
    """
    Extract client IP address from request.
    Uses consolidated logic from utils.net for security.
    """
    try:
        headers = getattr(request, "headers", None) or {}
        if isinstance(headers, dict):
            xff = headers.get("X-Forwarded-For") or headers.get("x-forwarded-for")
            if xff:
                ip = str(xff).split(",")[0].strip()
                return ip or None
    except Exception:
        pass

    try:
        client = getattr(request, "client", None)
        host = getattr(client, "host", None) if client else None
        if host:
            return str(host)
    except Exception:
        pass

    try:
        return get_client_ip(request)
    except Exception:
        return None


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
                or permissions.get("admin", False)
            )

    # Also recognise admin dashboard sessions (admin_sessions table)
    # which set request.state.admin_id but not request.state.user.
    if not user_info["is_admin"]:
        admin_id = getattr(request.state, "admin_id", None)
        if admin_id is not None:
            user_info["is_admin"] = True
            if not user_info["user_id"]:
                user_info["user_id"] = admin_id

    # Secure bypass check — requires non-empty bypass_secret.
    bypass_secret = config.get("rate_limiting.bypass_secret")
    if bypass_secret:
        bypass_header = request.headers.get("X-RateLimit-Bypass")
        if bypass_header and hmac.compare_digest(bypass_header, bypass_secret):
            user_info["is_internal"] = True

    if getattr(request.state, "is_internal", False) is True:
        # Only trust is_internal when set server-side (not from client headers)
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
        "/status",
        "/api/v1/status",
        "/api/v1/health",
        "/docs",
        "/redoc",
        "/openapi.json",
    ]
    # Admin API paths are excluded from general rate limiting.
    # Admin login has its own rate limiting (in authenticate_admin)
    # that returns proper 429 status codes.
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
