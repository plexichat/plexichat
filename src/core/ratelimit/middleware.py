"""
FastAPI middleware for rate limiting.
"""

import hashlib
import re
import threading
from typing import Optional, Dict, Callable, Any

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp

import utils.config as config
from src.utils.net import get_client_ip


# ---------------------------------------------------------------------------
# F8: bounded rate-limit keyspace.
# ---------------------------------------------------------------------------
# ``extract_route_info`` used to return ``f"{method} {path}"`` verbatim for
# any path that matched no known route. An attacker could therefore craft
# distinct URLs (``/api/a?x=1..N``, ``/api/evil/<id>``) and each one created
# a brand-new bucket row in the persistent backends (DB/Valkey) with no
# eviction -- unbounded storage growth / DoS via key exhaustion.
#
# The fix normalizes attacker-controlled input BEFORE it becomes a bucket
# key, and bounds whatever keyspace remains:
#   1. the query string / fragment is stripped (``?`` / ``#`` never fan out);
#   2. UUID-like, pure-numeric, and long-hex path segments collapse to
#      ``{id}`` so ``/api/evil/1..N`` shares one bucket;
#   3. overlong paths are truncated to ``MAX_ROUTE_LENGTH``;
#   4. genuinely-distinct unknown routes are kept 1:1 only up to
#      ``MAX_DISTINCT_UNKNOWN_ROUTES`` -- beyond that they hash into a fixed
#      set of ``UNKNOWN_BUCKET_COUNT`` overflow buckets, so 1000 distinct
#      paths can never create 1000 buckets.  Legitimate low-cardinality
#      routes still get distinct buckets (per-route limiting keeps working).
# Every storage backend additionally enforces TTL + row-count eviction, so
# even a full overflow set cannot grow without bound.
MAX_ROUTE_LENGTH = 512
MAX_DISTINCT_UNKNOWN_ROUTES = 128
UNKNOWN_BUCKET_COUNT = 16

_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_HEXISH_RE = re.compile(r"^[0-9a-fA-F]{16,}$")

_unknown_lock = threading.Lock()
# Insertion-ordered set of distinct normalized unknown routes (py3.7+ dict).
_unknown_routes: Dict[str, None] = {}


def _reset_unknown_route_cache() -> None:
    """Clear the bounded unknown-route registry (used by tests)."""
    with _unknown_lock:
        _unknown_routes.clear()


def _collapse_id_segments(path: str) -> str:
    """Replace ID-like path segments with ``{id}``."""
    segments = path.split("/")
    collapsed = []
    for seg in segments:
        if not seg:
            collapsed.append(seg)
            continue
        if seg.isdigit():
            collapsed.append("{id}")
        elif _UUID_RE.match(seg):
            collapsed.append("{id}")
        elif _HEXISH_RE.match(seg):
            collapsed.append("{id}")
        else:
            # Cap absurd single-segment lengths so one giant segment
            # cannot blow up key memory.
            collapsed.append(seg[:64] if len(seg) > 64 else seg)
    return "/".join(collapsed)


def _register_unknown_route(candidate: str, method: str) -> str:
    """Bound the distinct unknown-route keyspace via hashed overflow."""
    with _unknown_lock:
        if candidate in _unknown_routes:
            return candidate
        if len(_unknown_routes) < MAX_DISTINCT_UNKNOWN_ROUTES:
            _unknown_routes[candidate] = None
            return candidate
    digest = hashlib.sha256(candidate.encode("utf-8")).hexdigest()
    slot = int(digest[:8], 16) % UNKNOWN_BUCKET_COUNT
    return f"{method} /unknown/{{overflow-{slot}}}"


_DEFAULT_API_PREFIX = "/api/v1"


def _resolve_api_prefix() -> str:
    """Resolve the configurable API prefix, falling back to the default.

    ``extract_route_info``'s route tables are keyed on the default prefix.
    An operator that renames ``api.api_prefix`` would otherwise miss every
    strict per-route cap (login/register/2FA/password-reset) and silently
    fall back to the generic default limit.
    """
    try:
        prefix = config.get("api", {}).get("api_prefix", _DEFAULT_API_PREFIX)
        return (prefix or _DEFAULT_API_PREFIX).rstrip("/") or _DEFAULT_API_PREFIX
    except Exception:
        return _DEFAULT_API_PREFIX


# Common routes for fast lookup (without regex)
COMMON_ROUTES = {
    "/api/v1/auth/login": "POST /auth/login",
    "/api/v1/auth/register": "POST /auth/register",
    "/api/v1/auth/2fa": "POST /auth/2fa",
    # SECURITY: strict brute-force protections for token/code confirmation
    # endpoints. Without these mappings the requests fall through to the
    # generic per-IP default instead of their intended strict caps.
    "/api/v1/auth/2fa/enable": "POST /auth/2fa/enable",
    "/api/v1/auth/2fa/confirm": "POST /auth/2fa/confirm",
    "/api/v1/auth/2fa/disable": "POST /auth/2fa/disable",
    "/api/v1/auth/password-reset/request": "POST /auth/password-reset/request",
    "/api/v1/auth/password-reset/confirm": "POST /auth/password-reset/confirm",
    "/api/v1/auth/refresh": "POST /auth/refresh",
    "/api/v1/auth/ws-ticket": "POST /auth/ws-ticket",
    "/api/v1/auth/verify-email": "{method} /auth/verify-email",
    "/api/v1/auth/resend-verification": "POST /auth/resend-verification",
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
    (re.compile(r"^/api/v\d+/suite/stash/items$"), "{method} /suite/stash/items"),
    (
        re.compile(r"^/api/v\d+/suite/stash/items/(\d+)/versions$"),
        "{method} /suite/stash/items/{id}/versions",
    ),
    (
        re.compile(r"^/api/v\d+/suite/stash/items/(\d+)/pin$"),
        "{method} /suite/stash/items/{id}/pin",
    ),
    (
        re.compile(r"^/api/v\d+/suite/stash/items/(\d+)/grants$"),
        "{method} /suite/stash/items/{id}/grants",
    ),
    (
        re.compile(r"^/api/v\d+/suite/stash/items/(\d+)/download-ticket$"),
        "{method} /suite/stash/items/{id}/download-ticket",
    ),
    (
        re.compile(r"^/api/v\d+/suite/stash/items/(\d+)/restore$"),
        "{method} /suite/stash/items/{id}/restore",
    ),
    (
        re.compile(r"^/api/v\d+/suite/stash/items/(\d+)$"),
        "{method} /suite/stash/items/{id}",
    ),
    (
        re.compile(r"^/api/v\d+/suite/stash/download-ticket/redeem$"),
        "{method} /suite/stash/download-ticket/redeem",
    ),
    (
        re.compile(r"^/api/v\d+/suite/stash/grants/(\d+)$"),
        "{method} /suite/stash/grants/{id}",
    ),
    (
        re.compile(r"^/api/v\d+/suite/stash/collections$"),
        "{method} /suite/stash/collections",
    ),
    (
        re.compile(r"^/api/v\d+/suite/sum/workbooks/(\d+)/sheets/(\d+)/viewport$"),
        "{method} /suite/sum/workbooks/{id}/sheets/{sheet_id}/viewport",
    ),
    (
        re.compile(r"^/api/v\d+/suite/sum/workbooks/(\d+)/sheets/(\d+)/operations$"),
        "{method} /suite/sum/workbooks/{id}/sheets/{sheet_id}/operations",
    ),
    (
        re.compile(r"^/api/v\d+/suite/sum/workbooks/(\d+)/restore$"),
        "{method} /suite/sum/workbooks/{id}/restore",
    ),
    (
        re.compile(r"^/api/v\d+/suite/sum/workbooks/(\d+)$"),
        "{method} /suite/sum/workbooks/{id}",
    ),
    (re.compile(r"^/api/v\d+/suite/sum/workbooks$"), "{method} /suite/sum/workbooks"),
    (re.compile(r"^/api/v\d+/telemetry/response-times$"), "POST /telemetry"),
    # OAuth provider routes (provider is a path segment, not an ID)
    (
        re.compile(r"^/api/v\d+/auth/oauth/([^/]+)/login$"),
        "{method} /auth/oauth/login",
    ),
    (
        re.compile(r"^/api/v\d+/auth/oauth/([^/]+)/callback$"),
        "{method} /auth/oauth/callback",
    ),
]


def extract_route_info(path: str, method: str) -> tuple:
    """
    Extract route pattern and resource ID from request path.

    Returns:
        Tuple of (route_pattern, resource_id, webhook_id).
    """
    # F8: strip query string / fragment first -- they are attacker-controlled
    # and must never fan out into distinct buckets. (ASGI ``scope["path"]``
    # is already decoded without the query string, but callers/tests may
    # pass a full path, so normalize defensively.)
    path = path.split("?", 1)[0].split("#", 1)[0]
    if not path.startswith("/"):
        path = "/" + path
    # Collapse duplicate slashes and drop a trailing slash (except root).
    path = re.sub(r"/{2,}", "/", path)
    if len(path) > 1 and path.endswith("/"):
        path = path[:-1]
    # Hard cap on raw path length before any further processing.
    if len(path) > MAX_ROUTE_LENGTH:
        path = path[:MAX_ROUTE_LENGTH]

    # Normalize a non-default api_prefix back to the canonical prefix the
    # route tables below are keyed on, so renamed deployments still match
    # their strict per-route caps.
    api_prefix = _resolve_api_prefix()
    if api_prefix != _DEFAULT_API_PREFIX and path.startswith(api_prefix):
        path = _DEFAULT_API_PREFIX + path[len(api_prefix) :]

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
    # 3. Unknown path: collapse ID-like segments, then bound the keyspace.
    normalized = _collapse_id_segments(path)
    if len(normalized) > MAX_ROUTE_LENGTH:
        normalized = normalized[:MAX_ROUTE_LENGTH]
    candidate = f"{method} {normalized}"
    return _register_unknown_route(candidate, method), resource_id, webhook_id


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

            # SECURITY: hmac.compare_digest raises TypeError on non-ASCII
            # str inputs (Starlette decodes headers as latin-1, so a client
            # can send arbitrary bytes). Compare UTF-8 encodings so a
            # malformed header is simply treated as "no bypass" instead of
            # raising a 500 inside the ASGI middleware.
            if (
                bypass_secret
                and bypass_header
                and hmac.compare_digest(
                    bypass_header.encode("utf-8"), str(bypass_secret).encode("utf-8")
                )
            ):
                user_info["is_internal"] = True
            elif getattr(request.state, "is_internal", False):
                user_info["is_internal"] = True

        route, resource_id, webhook_id = extract_route_info(path, method)
        if webhook_id is not None:
            user_info["is_webhook"] = True
            user_info["webhook_id"] = webhook_id

        result = await ratelimit.acheck_rate_limit(
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
