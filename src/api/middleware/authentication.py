"""
Authentication middleware - Token validation and user extraction.
"""

from typing import Optional
import re
from fastapi import Request, HTTPException
from fastapi.security import HTTPBearer
from starlette.responses import JSONResponse
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Receive, Send, Scope

import src.api as api
from src.core.auth.models import TokenInfo
from src.core.auth.exceptions import AuthError

from .policy_cache import get_static_policy
from .logging import _safe_log_path
from .request_context import (
    bind_current_context,
    cached_client_ip,
    cached_is_self_test,
    get_request_user,
    lookup_token_result,
    remember_token_result,
    reset_current_context,
    set_request_user,
)

import utils.logger as logger

security = HTTPBearer(auto_error=False)

# ``re.fullmatch(pattern_string, ...)`` rebuilds the cache key and routes
# through the module-level wrapper on every call. Keep the compiled pattern
# once; the restrictive Bearer/Bot grammar itself still lives inside
# ``_extract_token`` so it stays auditable at the point of use.
_TOKEN_PATTERN_CACHE: dict = {}


class AuthenticationMiddleware:
    """ASGI middleware for hardened token validation."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # Bind this request's cache to the current task so dependencies (even
        # helpers that only receive a token, such as media signing) reuse the
        # token/IP checks resolved here. Reset in ``finally`` so a worker task
        # never carries one request's facts into the next.
        ctx_token = bind_current_context(scope)
        try:
            await self._process(scope, receive, send)
        finally:
            reset_current_context(ctx_token)

    async def _process(self, scope: Scope, receive: Receive, send: Send) -> None:
        cookie_token: Optional[str] = None
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        if "state" not in scope:
            scope["state"] = {}

        # 1. Check for Internal Service / Self-Test Authentication (HTTP only for now)
        if scope["type"] == "http":
            # Create request from scope ONLY to avoid consuming the 'receive' stream
            request = Request(scope)
            path = scope.get("path", "")

            # Use centralized self-test validation which enforces:
            # 1. Request originates from localhost (127.0.0.1 or ::1)
            # 2. X-Plexichat-Internal-Secret matches via hmac.compare_digest
            is_internal = cached_is_self_test(request)

            if is_internal:
                scope["state"]["is_internal"] = True

                # Also process Bearer token for internal/self-test requests
                # so authenticated endpoints have user context.
                # Only rate limits are bypassed (via is_internal), not auth.
                auth_header = request.headers.get("Authorization")
                if auth_header:
                    token = self._extract_token(auth_header)
                    if token:
                        auth = api.get_auth()
                        if auth:
                            from src.utils.net import get_client_ip

                            ip = cached_client_ip(request, resolver=get_client_ip)
                            ua = request.headers.get("User-Agent")

                            def _verify_token(token_str, client_ip, user_agent):
                                return auth.verify_token(
                                    token_str,
                                    ip_address=client_ip,
                                    user_agent=user_agent,
                                    is_selftest=True,
                                )

                            from fastapi.concurrency import run_in_threadpool

                            def _verify_token_cached():
                                found, cached = lookup_token_result(token, obj=scope)
                                if found:
                                    return cached
                                result = _verify_token(token, ip, ua)
                                remember_token_result(token, result, obj=scope)
                                return result

                            try:
                                token_info = await run_in_threadpool(
                                    _verify_token_cached
                                )

                                if token_info:
                                    set_request_user(scope, token_info)
                            except Exception as e:
                                if isinstance(e, AuthError):
                                    logger.warning(
                                        f"Auth: Internal token verification AuthError for path {path}: {e}"
                                    )
                                else:
                                    logger.error(
                                        f"Auth: Internal system error during token verification for path {path}: {e}",
                                        exc_info=True,
                                    )

                # Early return — rate limits already bypassed via is_internal
                await self.app(scope, receive, send)
                return

        # 2. Extract and Verify Bearer Token (HTTP only)
        # WebSockets handle their own auth during the handshake or via subprotocol
        if scope["type"] == "http":
            request = Request(scope)
            path = scope.get("path", "")

            # Derived routing policy (prefix, public endpoints, docs path) is
            # static until configuration changes. Build it once and cache it
            # with safe invalidation instead of rebuilding per request. The
            # prefix-independent entries stay literal; admin paths are still
            # resolved here because they may be patched at runtime.
            policy = get_static_policy()
            api_prefix = policy.api_prefix

            # Fast path: every gate below is scoped to the API prefix (the
            # access-token gate, bearer verification and the cookie fallback
            # all require it), so a non-API route that presents no credentials
            # cannot be authenticated here. Skip the admin/docs/config work
            # for static assets and other non-API traffic.
            if not path.startswith(api_prefix) and not request.headers.get(
                "Authorization"
            ):
                await self.app(scope, receive, send)
                return

            # Skip for admin routes which manage their own sessions.
            admin_paths = ["/admin"]
            admin_path = "/admin"
            try:
                import utils.config as config

                admin_path = config.get("admin_ui", {}).get("path", "/admin")
            except Exception:
                admin_path = "/admin"
            if not admin_path.startswith("/"):
                admin_path = f"/{admin_path}"
            admin_paths.append(admin_path)
            if api_prefix:
                admin_paths.append(f"{api_prefix}{admin_path}")
            is_admin_path = any(
                path == candidate or path.startswith(f"{candidate}/")
                for candidate in admin_paths
            )

            docs_path = policy.docs_path
            is_docs_path = bool(
                docs_path and (path == docs_path or path.startswith(f"{docs_path}/"))
            )

            path_parts = path.split("/")
            is_webhook_execute = (
                scope.get("method") == "POST"
                and path.startswith(policy.webhook_execute_prefix)
                and len(path_parts) == 6
                and path_parts[-1] != "regenerate-token"
            )

            is_public_endpoint = (
                path in policy.public_endpoints
                or is_docs_path
                or path.startswith(policy.password_requirements_prefix)
                or is_webhook_execute
            )

            if (
                not is_admin_path
                and not is_public_endpoint
                and not path.startswith(f"{api_prefix}/avatars/")
                and path.startswith(api_prefix)
            ):
                auth = api.get_auth()
                if auth:
                    auth_manager = auth
                    from fastapi.concurrency import run_in_threadpool

                    def _require_with_cleanup(auth_ref=auth_manager):
                        db = api.get_db()
                        try:
                            return auth_ref.is_api_access_token_required()
                        finally:
                            if db:
                                db.close()

                    access_required = await run_in_threadpool(_require_with_cleanup)
                    if access_required:
                        access_token = request.headers.get("X-API-Access-Token")
                        if not access_token:
                            limited = await self._ip_fallback_rate_limit(request)
                            if limited is not None:
                                await limited(scope, receive, send)
                                return
                            response = JSONResponse(
                                status_code=401,
                                content={
                                    "error": {
                                        "code": 401,
                                        "message": "API access token required",
                                    }
                                },
                            )
                            await response(scope, receive, send)
                            return

                        from src.utils.net import get_client_ip

                        client_ip = cached_client_ip(request, resolver=get_client_ip)
                        client_user_agent = request.headers.get("User-Agent")
                        request_path = request.url.path
                        request_method = request.method

                        def _verify_access_with_cleanup(
                            token_str, auth_ref=auth_manager
                        ):
                            db = api.get_db()
                            try:
                                return auth_ref.verify_api_access_token(
                                    token_str,
                                    ip_address=client_ip,
                                    user_agent=client_user_agent,
                                    path=request_path,
                                    method=request_method,
                                )
                            finally:
                                if db:
                                    db.close()

                        is_valid = await run_in_threadpool(
                            _verify_access_with_cleanup, access_token
                        )
                        if not is_valid:
                            limited = await self._ip_fallback_rate_limit(request)
                            if limited is not None:
                                await limited(scope, receive, send)
                                return
                            response = JSONResponse(
                                status_code=401,
                                content={
                                    "error": {
                                        "code": 401,
                                        "message": "Invalid API access token",
                                    }
                                },
                            )
                            await response(scope, receive, send)
                            return
                else:
                    response = JSONResponse(
                        status_code=500,
                        content={
                            "error": {
                                "code": 500,
                                "message": "Auth module not available",
                            }
                        },
                    )
                    await response(scope, receive, send)
                    return

            # Detect multiple Authorization headers (possible header injection)
            auth_header_count = sum(
                1
                for k, _ in scope.get("headers", [])
                if isinstance(k, (bytes, bytearray)) and k.lower() == b"authorization"
            )
            cookie_token = None
            if auth_header_count > 1:
                scope["state"]["auth_error"] = "Multiple Authorization headers"
                auth_header = None
            else:
                auth_header = request.headers.get("Authorization")
                if auth_header and "," in auth_header:
                    scope["state"]["auth_error"] = "Multiple Authorization headers"
                    auth_header = None

            if auth_header and not is_admin_path and not is_public_endpoint:
                token = self._extract_token(auth_header)
                if token:
                    auth = api.get_auth()
                    if auth:
                        try:
                            # SECURITY: Use hardened utility to extract real client IP from trusted proxies
                            from src.utils.net import get_client_ip

                            ip = cached_client_ip(request, resolver=get_client_ip)
                            ua = request.headers.get("User-Agent")

                            # Verify token in a threadpool to avoid blocking the event loop
                            def _verify_with_cleanup(
                                token_str, client_ip, user_agent, auth_ref=auth
                            ):
                                return auth_ref.verify_token(
                                    token_str,
                                    ip_address=client_ip,
                                    user_agent=user_agent,
                                )

                            from fastapi.concurrency import run_in_threadpool

                            def _verify_with_cache(token_str, client_ip, user_agent):
                                found, cached = lookup_token_result(
                                    token_str, obj=scope
                                )
                                if found:
                                    return cached
                                result = _verify_with_cleanup(
                                    token_str, client_ip, user_agent
                                )
                                remember_token_result(token_str, result, obj=scope)
                                return result

                            token_info = await run_in_threadpool(
                                _verify_with_cache, token, ip, ua
                            )

                            if token_info:
                                set_request_user(scope, token_info)
                                logger.debug(
                                    f"Auth: Successfully authenticated user {token_info.user_id} for path {path}"
                                )
                            else:
                                logger.warning(
                                    f"Auth: verify_token returned None for path {path}"
                                )
                                scope["state"]["auth_error"] = "Invalid token"
                        except Exception as e:
                            # Distinguish between AuthError (invalid token) and other errors (DB failed)
                            if isinstance(e, AuthError):
                                # This is a legitimate authentication failure (invalid/expired token)
                                logger.debug(
                                    f"Authentication failed for path {path}: {e}"
                                )
                                scope["state"]["auth_error"] = str(e)
                            else:
                                # This is likely a database or system error
                                # We should log it as an error and potentially return a 500 instead of 401
                                logger.error(
                                    f"System error during authentication for path {path}: {e}",
                                    exc_info=True,
                                )
                                scope["state"]["system_error"] = str(e)
                    else:
                        logger.error(f"Auth: Auth module NOT AVAILABLE for path {path}")
                        scope["state"]["system_error"] = "Auth module not available"
                else:
                    from utils.logger import mask_string

                    masked_header = mask_string(auth_header)
                    logger.debug(
                        f"Auth: Failed to extract token from header '{masked_header}' for path {path}"
                    )
                    scope["state"]["auth_error"] = "Invalid Authorization header"
            # Cookie fallback: runs when no Authorization header was sent OR
            # when the bearer token failed verification (F1). A valid HttpOnly
            # cookie session is not shadowed by a stale/invalid bearer header.
            _bearer_failed = bool(
                auth_header
                and not scope["state"].get("user")
                and not scope["state"].get("system_error")
            )
            if (
                (not auth_header or _bearer_failed)
                and not is_admin_path
                and not is_public_endpoint
                and scope.get("method") != "OPTIONS"
                and path.startswith(api_prefix)
                and not path.startswith(f"{api_prefix}/avatars/")
            ):
                # Cookie-based session fallback. Cookies are attached
                # automatically by the browser, so state-changing requests
                # authenticated this way must present the custom
                # ``X-Plexichat-Request`` marker header (a cross-origin
                # attacker cannot add it without passing CORS preflight).
                #
                # M2 defense-in-depth: the browser-set ``Sec-Fetch-Site``
                # header cannot be forged by a cross-origin page, so a
                # cross-site fetch carrying our cookie is a CSRF attempt even
                # if a CORS misconfiguration let the marker header through.
                # Non-browser clients omit Sec-Fetch-Site entirely; for those,
                # the marker-header requirement still applies.
                sec_fetch_site = request.headers.get("Sec-Fetch-Site")
                if sec_fetch_site == "cross-site":
                    csrf_ok = False
                elif sec_fetch_site == "same-origin":
                    # Only an EXACT same-origin request is safe to trust on the
                    # browser-set fetch-metadata signal alone: a cross-origin
                    # page cannot make its own request carry this value, and
                    # the header cannot be overwritten by web content.
                    csrf_ok = True
                else:
                    # ``same-site`` is NOT a CSRF boundary: any sibling
                    # subdomain under the same registrable domain (a shared
                    # CDN, user-content host, or sibling app) can send a
                    # credentialed request classified as same-site. ``none``
                    # is no stronger — it is emitted for initiator-less
                    # navigations and non-fetch contexts that a browser
                    # extension or a header-stripping proxy can produce.
                    # Missing/unknown values are equally unproven. For all of
                    # these, require BOTH the marker header AND an Origin/
                    # Referer that matches the configured CORS allowlist —
                    # the marker alone is a static, guessable constant, so a
                    # CORS misconfiguration or header-injecting plugin would
                    # otherwise complete a cross-site write.
                    csrf_ok = request.headers.get("X-Plexichat-Request") == "1"
                    if csrf_ok and not self._origin_allowed(request):
                        csrf_ok = False
                cookie_token = request.cookies.get("plexichat_token")
                if cookie_token:
                    mutating = scope.get("method") in ("POST", "PUT", "PATCH", "DELETE")
                    if mutating and not csrf_ok:
                        scope["state"]["auth_error"] = "CSRF validation failed"
                        logger.warning(
                            f"Auth: Cookie-authenticated mutating request missing X-Plexichat-Request header at {path}"
                        )
                    else:
                        auth = api.get_auth()
                        if auth:
                            try:
                                from src.utils.net import get_client_ip
                                from fastapi.concurrency import run_in_threadpool

                                ip = cached_client_ip(request, resolver=get_client_ip)
                                ua = request.headers.get("User-Agent")

                                def _verify_cookie(token_str, client_ip, user_agent):
                                    return auth.verify_token(
                                        token_str,
                                        ip_address=client_ip,
                                        user_agent=user_agent,
                                    )

                                def _verify_cookie_cached(token_str, client_ip, ua):
                                    found, cached = lookup_token_result(
                                        token_str, obj=scope
                                    )
                                    if found:
                                        return cached
                                    result = _verify_cookie(token_str, client_ip, ua)
                                    remember_token_result(token_str, result, obj=scope)
                                    return result

                                token_info = await run_in_threadpool(
                                    _verify_cookie_cached, cookie_token, ip, ua
                                )
                                if token_info:
                                    set_request_user(scope, token_info)
                                    scope["state"]["cookie_auth"] = True
                                    # Bearer fallback succeeded: a stale bearer no
                                    # longer shadows the valid cookie session (F1).
                                    scope["state"].pop("auth_error", None)
                                    logger.debug(
                                        f"Auth: Cookie-authenticated user {token_info.user_id} for path {path} (bearer fallback)"
                                        if _bearer_failed
                                        else f"Auth: Cookie-authenticated user {token_info.user_id} for path {path}"
                                    )
                            except AuthError as e:
                                logger.debug(f"Cookie auth failed for {path}: {e}")
                                scope["state"]["auth_error"] = str(e)
                            except Exception as e:
                                logger.error(
                                    f"System error during cookie authentication for path {path}: {e}",
                                    exc_info=True,
                                )
                                scope["state"]["system_error"] = str(e)

        # Rolling cookie: re-issue the session cookie on successful
        # cookie-authenticated responses so its Max-Age tracks the (possibly
        # extended) DB session instead of silently expiring mid-session.
        if (
            scope["type"] == "http"
            and scope.get("state", {}).get("cookie_auth")
            and cookie_token
        ):
            # Track the authenticated session's own expiry (a trusted device's
            # row is longer-lived than the configured default).
            rolling_user = scope.get("state", {}).get("user")
            send = self._rolling_cookie_send_wrapper(
                send, cookie_token, getattr(rolling_user, "expires_at", None)
            )

        await self.app(scope, receive, send)

    @staticmethod
    def _origin_allowed(request) -> bool:
        """Validate the ``Origin``/``Referer`` against the CORS allowlist.

        Part of the M2 CSRF defense: when ``Sec-Fetch-Site`` is unavailable,
        a mutating cookie-authenticated request must present BOTH the
        marker header AND an Origin/Referer matching a configured CORS
        origin. Requests carrying neither header (curl, server-to-server
        clients using the Authorization header path) are NOT affected —
        this gate only runs inside the cookie-auth fallback.

        Returns True (allow) when:
        - no Origin AND no Referer is present (non-browser client), or
        - either header's scheme+host matches a configured CORS origin.
        """
        try:
            import utils.config as config

            cors_origins = config.get("api", {}).get("cors_origins", []) or []
        except Exception:
            cors_origins = []
        if isinstance(cors_origins, str):
            cors_origins = [cors_origins]

        origin = request.headers.get("Origin")
        referer = request.headers.get("Referer")
        if not origin and not referer:
            # Non-browser client or privacy-stripping proxy: the marker
            # header requirement remains the only browser-agnostic gate.
            return True

        candidates = []
        if origin:
            candidates.append(origin.rstrip("/"))
        if referer:
            # Referer carries a path; keep scheme+host(+port) only.
            from urllib.parse import urlsplit

            try:
                parts = urlsplit(referer)
                if parts.scheme and parts.netloc:
                    candidates.append(f"{parts.scheme}://{parts.netloc}".rstrip("/"))
            except ValueError:
                # ``urlsplit`` raises only for a malformed authority (bad port,
                # unbalanced IPv6 bracket). Such a header cannot name an
                # allowed origin, so it simply contributes no candidate.
                # SECURITY: a Referer is a full URL whose path/query can carry
                # live credentials (password-reset ``code``, OAuth ``code``,
                # signed-URL ``sig``, session tokens). Logging the raw value
                # wrote those straight to the rotated log files and the console
                # sink; the shared SanitizingFilter only masks a fixed set of
                # ``key=value`` names, so ``code=``/``sig=`` secrets survived
                # verbatim (CWE-532). Keep the diagnostic -- an unparsable
                # Referer was ignored for origin matching -- but never persist
                # the attacker-controlled URL, query string included.
                logger.debug("ignoring unparsable Referer origin (value omitted)")

        allowed = {str(o).rstrip("/") for o in cors_origins if o}
        return any(c in allowed for c in candidates)

    @staticmethod
    def _rolling_cookie_send_wrapper(
        send: Send, token_value: str, expires_at_ms: Optional[int] = None
    ) -> Send:
        from src.api.routes.auth.cookies import (
            SESSION_COOKIE_NAME,
            session_cookie_max_age,
            session_cookie_secure,
        )

        parts = [
            f"{SESSION_COOKIE_NAME}={token_value}",
            f"Max-Age={session_cookie_max_age(expires_at_ms)}",
            "Path=/",
            "HttpOnly",
            "SameSite=Lax",
        ]
        if session_cookie_secure():
            parts.append("Secure")
        cookie_header = "; ".join(parts)
        cookie_name = f"{SESSION_COOKIE_NAME}=".encode()

        async def wrapped_send(message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                status = message.get("status", 500)
                # Never shadow a session cookie the route already issued.
                # /auth/refresh rotates the token and sets the NEW cookie;
                # appending the presented (now rotated-away) token afterwards
                # made the browser store the dead value — browsers honour the
                # last Set-Cookie — so every later request 401'd until sign-in.
                already_issued = any(
                    key.lower() == b"set-cookie"
                    and value.lstrip().startswith(cookie_name)
                    for key, value in (message.get("headers") or [])
                )
                if 200 <= status < 400 and not already_issued:
                    headers.append("set-cookie", cookie_header)
            await send(message)

        return wrapped_send

    def _extract_token(self, auth_header: str) -> Optional[str]:
        pattern = r"(Bearer|Bot) ([A-Za-z0-9._-]+)"
        compiled = _TOKEN_PATTERN_CACHE.get(pattern)
        if compiled is None:
            compiled = re.compile(pattern)
            _TOKEN_PATTERN_CACHE[pattern] = compiled
        match = compiled.fullmatch(auth_header)
        if match:
            return match.group(2)
        return None

    async def _ip_fallback_rate_limit(self, request: Request):
        """IP-keyed rate-limit accounting for requests this middleware rejects.

        SECURITY: the rate limiter runs INSIDE this middleware, so a request
        short-circuited here (missing/invalid ``X-API-Access-Token``) never
        reaches the limiter and bad-token floods consumed zero bucket budget.
        Charge an IP-keyed bucket before rejecting; returns a 429 response
        when that IP is over budget, otherwise None.
        """
        try:
            from src.core import ratelimit
            from src.core.ratelimit.middleware import extract_route_info
            from src.utils.net import get_client_ip

            if not ratelimit.is_setup():
                return None
            # The main RateLimitMiddleware normalizes the request path into a
            # bounded route key with ``extract_route_info`` before it ever
            # reaches the storage backend (the F8 keyspace hardening). This
            # fallback charges the same backend but used to key on the RAW
            # ``method + path``: an attacker flooding bad
            # ``X-API-Access-Token`` requests at unique paths
            # (``/api/v1/evil/1``, ``/api/v1/evil/2`` ...) created a brand-new
            # persistent bucket row per path, bypassing the bounded keyspace
            # and exhausting rate-limit storage (DoS). Reuse the very same
            # normalization so the fallback cannot re-open that hole.
            route, resource_id, webhook_id = extract_route_info(
                request.url.path, request.method
            )
            result = await ratelimit.acheck_rate_limit(
                user_id=None,
                ip_address=cached_client_ip(request, resolver=get_client_ip)
                or "unknown",
                route=route,
                resource_id=resource_id,
                is_webhook=webhook_id is not None,
                webhook_id=webhook_id,
            )
            if not result.allowed:
                return JSONResponse(
                    status_code=429,
                    content=result.response_body,
                    headers=ratelimit.get_headers(result),
                )
        except Exception as e:
            # Never let limiter failures turn an auth decision into a 500.
            logger.debug(f"Auth-path fallback rate limit check failed: {e}")
        return None


async def get_current_user(request: Request) -> TokenInfo:
    """Dependency for mandatory authentication."""

    user = get_request_user(request)

    if not user:
        # Check if there was a system error (DB failed) vs auth error (invalid token)
        system_error = request.scope.get("state", {}).get("system_error")
        if system_error:
            logger.error(
                f"get_current_user: System error blocking authentication: {system_error}"
            )
            raise HTTPException(
                status_code=500,
                detail={
                    "error": {
                        "code": 500,
                        "message": "Internal server error during authentication",
                    }
                },
            )

        error = request.scope.get("state", {}).get(
            "auth_error", "Authentication required"
        )

        # SECURITY: request.url.path is the RESOLVED path. Authenticated routes
        # can carry a credential in a path segment (e.g. a push token at
        # /features/push/tokens/{token_value}); logging it verbatim on a failed
        # auth check bypasses the access-log redactor and writes that secret to
        # the sink. Route it through the shared redactor, which fails closed by
        # emitting the matched route template when routing metadata is present.
        logger.debug(
            f"get_current_user: No user in state for path "
            f"{_safe_log_path(request.scope, request.url.path)}. Error: {error}"
        )

        raise HTTPException(
            status_code=401,
            detail={"error": {"code": 401, "message": error}},
        )

    # Enforce account status (Locked or Forced Username Change)
    path = request.url.path

    # Account Lock check (returns 403)
    if user.account_locked:
        logger.warning(f"403 account_locked=True for user {user.user_id} at {path}")
        raise HTTPException(
            status_code=403,
            detail={
                "error": {
                    "code": 403,
                    "message": "Account suspended",
                    "reason": "Your account has been suspended by an administrator.",
                }
            },
        )

    # Account Deletion check (Hard Freeze)
    if getattr(user, "deletion_status", "active") == "frozen":
        logger.warning(f"403 deletion_status=frozen for user {user.user_id} at {path}")
        raise HTTPException(
            status_code=403,
            detail={
                "error": {
                    "code": 403,
                    "message": "Account scheduled for deletion",
                    "reason": "This account is scheduled for deletion and is currently frozen.",
                }
            },
        )

    # Forced Username Change check
    if user.force_username_change:
        logger.warning(
            f"403 force_username_change=True for user {user.user_id} at {path}"
        )
        # Allow GET @me (to see current status) and PATCH @me (to fix the username)
        # Also allow logout
        _api_prefix = get_static_policy().api_prefix
        is_me_path = path == f"{_api_prefix}/users/@me"
        is_allowed_me_method = request.method in ("GET", "PATCH")

        allowed = (
            (is_me_path and is_allowed_me_method)
            or path == f"{_api_prefix}/auth/logout"
            or path.startswith(f"{_api_prefix}/avatars/")
        )

        if not allowed:
            raise HTTPException(
                status_code=403,
                detail={
                    "error": {
                        "code": 403,
                        "message": "Username change required",
                        "reason": "You must change your username before you can continue using Plexichat.",
                    }
                },
            )

    return user


async def get_token_info(
    token: str,
    *,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> Optional[TokenInfo]:
    """Verify a raw token string and return token info when valid.

    When called during a request that already went through
    ``AuthenticationMiddleware`` the token was verified there; reuse that
    result instead of verifying the same credential a second time (signed
    media URLs call this after the auth middleware has run).

    The IP/User-Agent binding context MUST be forwarded on the cache-miss path.
    This helper used to call ``verify_token(token, None, None)``, skipping
    binding entirely: a credential the auth middleware rejected for a binding
    mismatch could be replayed against a signed media URL and accepted here.
    Callers handling a request pass the same context the middleware uses, so a
    miss re-verifies under that context and fails closed on a mismatch.
    """
    if not token:
        return None

    found, cached = lookup_token_result(token)
    if found:
        return cached

    auth = api.get_auth()
    if not auth:
        return None

    from fastapi.concurrency import run_in_threadpool

    def _verify_token(token_str: str):
        return auth.verify_token(token_str, ip_address, user_agent)

    try:
        result = await run_in_threadpool(_verify_token, token)
        remember_token_result(token, result)
        return result
    except Exception as e:
        # Log the exception to avoid silently masking system errors as "invalid token"
        logger.warning(
            f"get_token_info: Exception during token verification: {type(e).__name__}: {e}"
        )
        return None


async def get_optional_user(request: Request) -> Optional[TokenInfo]:
    """Dependency for optional authentication."""

    return get_request_user(request)
