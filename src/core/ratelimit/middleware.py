"""
FastAPI middleware for rate limiting.
"""

import re
from typing import Optional, Dict, Callable, Any

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp

import utils.config as config
from src.utils.net import get_client_ip


# Common routes for fast lookup (without regex)
COMMON_ROUTES = {
    "/api/v1/auth/login": "POST /auth/login",
    "/api/v1/auth/register": "POST /auth/register",
    "/api/v1/auth/2fa": "POST /auth/2fa",
    "/api/v1/auth/logout": "POST /auth/logout",
    "/api/v1/users/@me": "{method} /users/@me",
    "/api/v1/relationships/@me": "GET /relationships/@me",
    "/api/v1/relationships": "POST /relationships",
    "/api/v1/relationships/block": "POST /relationships/block",
    "/api/v1/media/upload": "POST /media/upload",
    "/api/v1/feedback": "POST /feedback",
}

ROUTE_PATTERNS = [
    (re.compile(r"^/api/v\d+/users/(\d+)$"), "GET /users/{id}"),
    (re.compile(r"^/api/v\d+/servers$"), "{method} /servers"),
    (re.compile(r"^/api/v\d+/servers/(\d+)$"), "{method} /servers/{id}"),
    (re.compile(r"^/api/v\d+/servers/(\d+)/channels$"), "GET /servers/{id}/channels"),
    (re.compile(r"^/api/v\d+/channels/(\d+)$"), "{method} /channels/{id}"),
    (
        re.compile(r"^/api/v\d+/channels/(\d+)/messages$"),
        "{method} /channels/{id}/messages",
    ),
    (
        re.compile(r"^/api/v\d+/channels/(\d+)/messages/(\d+)$"),
        "{method} /channels/{id}/messages/{msg_id}",
    ),
    (
        re.compile(r"^/api/v\d+/channels/(\d+)/messages/(\d+)/reactions/([^/]+)$"),
        "{method} /channels/{id}/messages/{msg_id}/reactions/{emoji}",
    ),
    (
        re.compile(r"^/api/v\d+/channels/(\d+)/messages/(\d+)/reactions$"),
        "GET /channels/{id}/messages/{msg_id}/reactions",
    ),
    (
        re.compile(r"^/api/v\d+/relationships/(\d+)/accept$"),
        "PUT /relationships/{id}/accept",
    ),
    (re.compile(r"^/api/v\d+/relationships/(\d+)$"), "DELETE /relationships/{id}"),
    (re.compile(r"^/api/v\d+/webhooks$"), "POST /webhooks"),
    (re.compile(r"^/api/v\d+/webhooks/(\d+)$"), "{method} /webhooks/{id}"),
    (re.compile(r"^/api/v\d+/webhooks/(\d+)/([^/]+)$"), "POST /webhooks/{id}/{token}"),
    (re.compile(r"^/api/v\d+/telemetry/response-times$"), "POST /telemetry"),
]


def extract_route_info(path: str, method: str) -> tuple:
    """
    Extract route pattern and resource ID from request path.

    Returns:
        Tuple of (route_pattern, resource_id, webhook_id).
    """
    # 1. Try fast lookup
    if path in COMMON_ROUTES:
        return COMMON_ROUTES[path].replace("{method}", method), None, None

    # 2. Try regex patterns
    resource_id = None
    webhook_id = None
    for pattern, route_template in ROUTE_PATTERNS:
        match = pattern.match(path)
        if match:
            route = route_template.replace("{method}", method)
            groups = match.groups()
            if groups:
                try:
                    if "webhooks" in route_template:
                        webhook_id = int(groups[0])
                    else:
                        resource_id = int(groups[0])
                except (ValueError, IndexError):
                    pass
            return route, resource_id, webhook_id
    return f"{method} {path}", resource_id, webhook_id


class RateLimitMiddlewareASGI:
    """Pure ASGI middleware for rate limiting."""

    def __init__(
        self,
        app: ASGIApp,
        get_user_info: Optional[Callable[[Request], Dict[str, Any]]] = None,
        exclude_paths: Optional[list] = None,
        include_headers_on_success: bool = True,
    ):
        """Initialize ASGI rate limit middleware."""
        self.app = app
        self._get_user_info = get_user_info
        self._exclude_paths = set(exclude_paths or [])
        self._include_headers_on_success = include_headers_on_success
        self._exclude_patterns = []

    def _should_exclude(self, path: str) -> bool:
        """Check if path should be excluded from rate limiting."""
        if path in self._exclude_paths:
            return True
        for pattern in self._exclude_patterns:
            if pattern.match(path):
                return True
        return False

    def _get_ip_address(self, request: Request) -> str:
        """Extract IP address using consolidated utility."""
        return get_client_ip(request) or "unknown"

    async def __call__(self, scope, receive, send):
        """ASGI interface."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        from src.core import ratelimit

        if not ratelimit.is_setup():
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        method = scope.get("method", "GET")

        # Skip OPTIONS requests (CORS preflight)
        if method == "OPTIONS":
            await self.app(scope, receive, send)
            return

        if self._should_exclude(path):
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive)

        if self._get_user_info:
            user_info = self._get_user_info(request)
        else:
            user_info = {
                "user_id": None,
                "ip_address": self._get_ip_address(request),
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
                user_info["is_bot"] = getattr(user, "token_type", "") == "bot"
                permissions = getattr(user, "permissions", {})
                user_info["is_admin"] = permissions.get(
                    "admin.*", False
                ) or permissions.get("*", False)

            # Bypass check (constant-time comparison to prevent timing attacks)
            rl_config = config.get("rate_limiting", {})
            bypass_secret = rl_config.get("bypass_secret")
            bypass_header = request.headers.get("X-RateLimit-Bypass")

            import hmac

            if (
                bypass_secret
                and bypass_header
                and hmac.compare_digest(bypass_header, bypass_secret)
            ):
                user_info["is_internal"] = True
            elif getattr(request.state, "is_internal", False):
                user_info["is_internal"] = True

        route, resource_id, webhook_id = extract_route_info(path, method)
        if webhook_id is not None:
            user_info["is_webhook"] = True
            user_info["webhook_id"] = webhook_id

        result = ratelimit.check_rate_limit(
            user_id=user_info.get("user_id"),
            ip_address=user_info.get("ip_address"),
            route=route,
            resource_id=resource_id,
            is_bot=user_info.get("is_bot", False),
            is_webhook=user_info.get("is_webhook", False),
            is_admin=user_info.get("is_admin", False),
            is_internal=user_info.get("is_internal", False),
            webhook_id=user_info.get("webhook_id"),
        )

        if not result.allowed:
            headers = ratelimit.get_headers(result)
            response = JSONResponse(
                status_code=429,
                content=result.response_body,
                headers=headers,
            )
            await response(scope, receive, send)
            return

        if not self._include_headers_on_success:
            await self.app(scope, receive, send)
            return

        # Handle headers on success
        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                rl_headers = ratelimit.get_headers(result)
                for key, value in rl_headers.items():
                    headers.append((key.lower().encode(), value.encode()))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_wrapper)


# Alias for backward compatibility with tests
RateLimitMiddleware = RateLimitMiddlewareASGI
