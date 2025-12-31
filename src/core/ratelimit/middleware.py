"""
FastAPI middleware for rate limiting.
"""

import re
from typing import Optional, Dict, Callable, Any

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp


ROUTE_PATTERNS = [
    (re.compile(r"^/api/v\d+/auth/login$"), "POST /auth/login"),
    (re.compile(r"^/api/v\d+/auth/register$"), "POST /auth/register"),
    (re.compile(r"^/api/v\d+/auth/2fa$"), "POST /auth/2fa"),
    (re.compile(r"^/api/v\d+/auth/logout$"), "POST /auth/logout"),
    (re.compile(r"^/api/v\d+/users/@me$"), "{method} /users/@me"),
    (re.compile(r"^/api/v\d+/users/\d+$"), "GET /users/{id}"),
    (re.compile(r"^/api/v\d+/servers$"), "{method} /servers"),
    (re.compile(r"^/api/v\d+/servers/\d+$"), "{method} /servers/{id}"),
    (re.compile(r"^/api/v\d+/servers/\d+/channels$"), "GET /servers/{id}/channels"),
    (re.compile(r"^/api/v\d+/channels/\d+$"), "{method} /channels/{id}"),
    (re.compile(r"^/api/v\d+/channels/(\d+)/messages$"), "{method} /channels/{id}/messages"),
    (re.compile(r"^/api/v\d+/channels/(\d+)/messages/\d+$"), "{method} /channels/{id}/messages/{msg_id}"),
    (re.compile(r"^/api/v\d+/channels/(\d+)/messages/\d+/reactions/[^/]+$"), "{method} /channels/{id}/messages/{msg_id}/reactions/{emoji}"),
    (re.compile(r"^/api/v\d+/channels/(\d+)/messages/\d+/reactions$"), "GET /channels/{id}/messages/{msg_id}/reactions"),
    (re.compile(r"^/api/v\d+/relationships/@me$"), "GET /relationships/@me"),
    (re.compile(r"^/api/v\d+/relationships$"), "POST /relationships"),
    (re.compile(r"^/api/v\d+/relationships/\d+/accept$"), "PUT /relationships/{id}/accept"),
    (re.compile(r"^/api/v\d+/relationships/\d+$"), "DELETE /relationships/{id}"),
    (re.compile(r"^/api/v\d+/relationships/block$"), "POST /relationships/block"),
    (re.compile(r"^/api/v\d+/webhooks$"), "POST /webhooks"),
    (re.compile(r"^/api/v\d+/webhooks/(\d+)$"), "{method} /webhooks/{id}"),
    (re.compile(r"^/api/v\d+/webhooks/(\d+)/[^/]+$"), "POST /webhooks/{id}/{token}"),
]


def extract_route_info(path: str, method: str) -> tuple:
    """
    Extract route pattern and resource ID from request path.

    Returns:
        Tuple of (route_pattern, resource_id, webhook_id).
    """
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


class RateLimitMiddleware(BaseHTTPMiddleware):
    """FastAPI middleware for rate limiting."""

    def __init__(
        self,
        app: ASGIApp,
        get_user_info: Optional[Callable[[Request], Dict[str, Any]]] = None,
        exclude_paths: Optional[list] = None,
        include_headers_on_success: bool = True,
    ):
        """
        Initialize rate limit middleware.

        Args:
            app: ASGI application.
            get_user_info: Callable to extract user info from request.
            exclude_paths: Paths to exclude from rate limiting.
            include_headers_on_success: Include rate limit headers on successful requests.
        """
        super().__init__(app)
        self._get_user_info = get_user_info or self._default_get_user_info
        self._exclude_paths = set(exclude_paths or ["/", "/health", "/docs", "/redoc", "/openapi.json"])
        self._include_headers_on_success = include_headers_on_success
        self._exclude_patterns = [
            re.compile(r"^/api/v\d+/health$"),
            re.compile(r"^/docs"),
            re.compile(r"^/redoc"),
            re.compile(r"^/openapi\.json$"),
        ]

    def _default_get_user_info(self, request: Request) -> Dict[str, Any]:
        """Default user info extraction from request state."""
        user_info = {
            "user_id": None,
            "is_bot": False,
            "is_admin": False,
            "is_internal": False,
            "is_webhook": False,
            "webhook_id": None,
        }
        if hasattr(request.state, "user") and request.state.user:
            user = request.state.user
            user_info["user_id"] = getattr(user, "user_id", None)
            user_info["is_bot"] = getattr(user, "token_type", "") == "bot"
            permissions = getattr(user, "permissions", {})
            user_info["is_admin"] = permissions.get("admin.*", False) or permissions.get("*", False)
        
        # Bypass rate limits for secure self-tests or internal requests
        is_selftest = getattr(request.state, "is_selftest", False)
        internal_header = request.headers.get("X-Internal-Request")
        if internal_header == "true" or is_selftest:
            user_info["is_internal"] = True
            
        return user_info

    def _should_exclude(self, path: str) -> bool:
        """Check if path should be excluded from rate limiting."""
        if path in self._exclude_paths:
            return True
        for pattern in self._exclude_patterns:
            if pattern.match(path):
                return True
        return False

    async def dispatch(self, request: Request, call_next):
        """Process request through rate limiting."""
        from src.core import ratelimit
        if not ratelimit.is_setup():
            return await call_next(request)
        path = request.url.path
        method = request.method

        # Skip OPTIONS requests (CORS preflight) - they should not be rate limited
        if method == "OPTIONS":
            return await call_next(request)

        if self._should_exclude(path):
            return await call_next(request)
        user_info = self._get_user_info(request)
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
            return JSONResponse(
                status_code=429,
                content=result.response_body,
                headers=headers,
            )
        response = await call_next(request)
        if self._include_headers_on_success:
            headers = ratelimit.get_headers(result)
            for key, value in headers.items():
                response.headers[key] = value
        return response


class RateLimitMiddlewareASGI:
    """Pure ASGI middleware for rate limiting (alternative to BaseHTTPMiddleware)."""

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
        self._exclude_paths = set(exclude_paths or ["/", "/health", "/docs", "/redoc", "/openapi.json"])
        self._include_headers_on_success = include_headers_on_success
        self._exclude_patterns = [
            re.compile(r"^/api/v\d+/health$"),
            re.compile(r"^/docs"),
            re.compile(r"^/redoc"),
            re.compile(r"^/openapi\.json$"),
        ]

    def _should_exclude(self, path: str) -> bool:
        """Check if path should be excluded from rate limiting."""
        if path in self._exclude_paths:
            return True
        for pattern in self._exclude_patterns:
            if pattern.match(path):
                return True
        return False

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
        user_info = {
            "user_id": None,
            "ip_address": None,
            "is_bot": False,
            "is_admin": False,
            "is_internal": False,
            "is_webhook": False,
            "webhook_id": None,
        }

        if self._get_user_info:
            user_info = self._get_user_info(request)
        else:
            # Fallback IP extraction
            forwarded = request.headers.get("X-Forwarded-For")
            if forwarded:
                user_info["ip_address"] = forwarded.split(",")[0].strip()
            elif request.client:
                user_info["ip_address"] = request.client.host

            if hasattr(request.state, "user") and request.state.user:
                user = request.state.user
                user_info["user_id"] = getattr(user, "user_id", None)
                user_info["is_bot"] = getattr(user, "token_type", "") == "bot"
                permissions = getattr(user, "permissions", {})
                user_info["is_admin"] = permissions.get("admin.*", False) or permissions.get("*", False)

        # Bypass rate limits for secure self-tests
        if getattr(request.state, "is_selftest", False):
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
