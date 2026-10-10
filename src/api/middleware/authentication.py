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

import utils.logger as logger

security = HTTPBearer(auto_error=False)


def _resolve_api_prefix() -> str:
    """Resolve the configurable API prefix, falling back to the default.

    Request-path comparisons in this middleware must follow
    ``api.api_prefix``; hardcoded ``/api/v1`` literals break as soon as an
    operator renames the prefix.
    """
    try:
        from src.api.config import get_api_config

        return get_api_config().api_prefix.rstrip("/") or "/api/v1"
    except Exception:
        return "/api/v1"


class AuthenticationMiddleware:
    """ASGI middleware for hardened token validation."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
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
            is_internal = api.is_self_test_request(request)

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

                            ip = get_client_ip(request)
                            ua = request.headers.get("User-Agent")

                            def _verify_token(token_str, client_ip, user_agent):
                                return auth.verify_token(
                                    token_str,
                                    ip_address=client_ip,
                                    user_agent=user_agent,
                                    is_selftest=True,
                                )

                            from fastapi.concurrency import run_in_threadpool

                            try:
                                token_info = await run_in_threadpool(
                                    _verify_token, token, ip, ua
                                )

                                if token_info:
                                    scope["state"]["user"] = token_info
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

            # Skip for admin routes which manage their own sessions, and public status/health routes
            admin_paths = ["/admin"]
            admin_path = "/admin"
            api_prefix = _resolve_api_prefix()
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

            # M3: these paths must follow the CONFIGURABLE api_prefix. The old
            # hardcoded /api/v1 literals stopped matching as soon as an
            # operator changed api_prefix, silently locking users out of
            # login/register (or, worse, turning the endpoint list into a
            # permissive no-op if the prefix was renamed). Prefix-independent
            # health/docs entries stay literal.
            public_endpoints = [
                f"{api_prefix}/auth/login",
                f"{api_prefix}/auth/register",
                f"{api_prefix}/auth/verify-email",
                f"{api_prefix}/auth/resend-verification",
                f"{api_prefix}/status",
                f"{api_prefix}/health",
                f"{api_prefix}/capabilities",
                f"{api_prefix}/version",
                f"{api_prefix}/auth/password-requirements",
                f"{api_prefix}/telemetry/csp-report",
                f"{api_prefix}/public/license/info",
                f"{api_prefix}/public/license/challenge",
                f"{api_prefix}/public/license/export",
                "/health",
                "/status",
                "/docs",
                "/redoc",
                "/openapi.json",
                "/",
            ]
            is_docs_path = False
            try:
                from src.api.routes.docs import get_docs_config

                docs_path = get_docs_config().path or "/docs/api"
                docs_path = docs_path if docs_path.startswith("/") else f"/{docs_path}"
                docs_path = docs_path.rstrip("/")
                if path == docs_path or path.startswith(f"{docs_path}/"):
                    is_docs_path = True
            except Exception:
                is_docs_path = False

            path_parts = path.split("/")
            is_webhook_execute = (
                scope.get("method") == "POST"
                and path.startswith(f"{api_prefix}/webhooks/")
                and len(path_parts) == 6
                and path_parts[-1] != "regenerate-token"
            )

            is_public_endpoint = (
                path in public_endpoints
                or is_docs_path
                or path.startswith(f"{api_prefix}/auth/password-requirements")
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

                        client_ip = get_client_ip(request)
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

                            ip = get_client_ip(request)
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

                            token_info = await run_in_threadpool(
                                _verify_with_cleanup, token, ip, ua
                            )

                            if token_info:
                                scope["state"]["user"] = token_info
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
                elif sec_fetch_site in ("same-origin", "same-site", "none"):
                    # ``same-origin``/``same-site`` cannot be forged by a
                    # cross-origin page; ``none`` marks direct user agent
                    # navigation (no fetch initiator).
                    csrf_ok = True
                else:
                    # M2: browsers that do not implement Sec-Fetch-* (or
                    # non-browser clients) fall through to the marker
                    # header PLUS an Origin/Referer allowlist check — the
                    # marker alone is a static, guessable constant, so a
                    # CORS misconfiguration or header-injecting plugin
                    # would otherwise complete a cross-site write.
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

                                ip = get_client_ip(request)
                                ua = request.headers.get("User-Agent")

                                def _verify_cookie(token_str, client_ip, user_agent):
                                    return auth.verify_token(
                                        token_str,
                                        ip_address=client_ip,
                                        user_agent=user_agent,
                                    )

                                token_info = await run_in_threadpool(
                                    _verify_cookie, cookie_token, ip, ua
                                )
                                if token_info:
                                    scope["state"]["user"] = token_info
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
            except Exception:
                pass

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

        async def wrapped_send(message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                status = message.get("status", 500)
                if 200 <= status < 400:
                    headers.append("set-cookie", cookie_header)
            await send(message)

        return wrapped_send

    def _extract_token(self, auth_header: str) -> Optional[str]:
        match = re.fullmatch(r"(Bearer|Bot) ([A-Za-z0-9._-]+)", auth_header)
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
            from src.utils.net import get_client_ip

            if not ratelimit.is_setup():
                return None
            result = await ratelimit.acheck_rate_limit(
                user_id=None,
                ip_address=get_client_ip(request) or "unknown",
                route=f"{request.method} {request.url.path}",
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

    user = request.scope.get("state", {}).get("user")

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

        logger.debug(
            f"get_current_user: No user in state for path {request.url.path}. Error: {error}"
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
        _api_prefix = _resolve_api_prefix()
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


async def get_token_info(token: str) -> Optional[TokenInfo]:
    """Verify a raw token string and return token info when valid."""
    if not token:
        return None

    auth = api.get_auth()
    if not auth:
        return None

    from fastapi.concurrency import run_in_threadpool

    def _verify_token(token_str: str):
        return auth.verify_token(token_str, None, None)

    try:
        return await run_in_threadpool(_verify_token, token)
    except Exception as e:
        # Log the exception to avoid silently masking system errors as "invalid token"
        logger.warning(
            f"get_token_info: Exception during token verification: {type(e).__name__}: {e}"
        )
        return None


async def get_optional_user(request: Request) -> Optional[TokenInfo]:
    """Dependency for optional authentication."""

    return request.scope.get("state", {}).get("user")
