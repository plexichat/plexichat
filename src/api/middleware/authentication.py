"""
Authentication middleware - Token validation and user extraction.
"""

from typing import Optional
import re
from fastapi import Request, HTTPException
from fastapi.security import HTTPBearer
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Send, Scope

import src.api as api
from src.core.auth.models import TokenInfo
from src.core.auth.exceptions import AuthError

security = HTTPBearer(auto_error=False)


class AuthenticationMiddleware:
    """ASGI middleware for hardened token validation."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        if "state" not in scope:
            scope["state"] = {}

        # 1. Check for Internal Service Authentication (HTTP only for now)
        is_internal = False
        if scope["type"] == "http":
            # Create request from scope ONLY to avoid consuming the 'receive' stream
            request = Request(scope)
            path = scope.get("path", "")

            import hmac
            internal_secret = api.get_internal_secret()
            provided_secret = request.headers.get("X-Plexichat-Internal-Secret")
            is_internal = bool(
                internal_secret
                and provided_secret
                and hmac.compare_digest(provided_secret, internal_secret)
            )

            if is_internal:
                import utils.logger as logger

                logger.debug(f"Auth: Internal service authenticated for path {path}")

        scope["state"]["is_internal"] = is_internal

        # 2. Extract and Verify Bearer Token (HTTP only)
        # WebSockets handle their own auth during the handshake or via subprotocol
        if scope["type"] == "http":
            request = Request(scope)
            path = scope.get("path", "")

            # Skip for admin routes which manage their own sessions, and public status/health routes
            admin_paths = ["/admin"]
            admin_path = "/admin"
            api_prefix = "/api/v1"
            try:
                from src.api.config import get_api_config

                api_prefix = get_api_config().api_prefix.rstrip("/") or "/api/v1"
            except Exception:
                api_prefix = "/api/v1"
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

            public_endpoints = [
                "/api/v1/status",
                "/api/v1/health",
                "/api/v1/capabilities",
                "/api/v1/version",
                "/api/v1/auth/password-requirements",
                "/api/v1/telemetry/csp-report",
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
                and path.startswith("/api/v1/webhooks/")
                and len(path_parts) == 6
                and path_parts[-1] != "regenerate-token"
            )

            is_public_endpoint = (
                path in public_endpoints
                or is_docs_path
                or path.startswith("/api/v1/auth/password-requirements")
                or is_webhook_execute
            )

            if is_internal:
                # Early exit for internal services - they don't need user tokens
                await self.app(scope, receive, send)
                return

            if (
                not is_admin_path
                and not is_public_endpoint
                and not path.startswith("/api/v1/avatars/")
                and path.startswith("/api/v1/")
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

                        def _verify_access_with_cleanup(
                            token_str, auth_ref=auth_manager
                        ):
                            db = api.get_db()
                            try:
                                return auth_ref.verify_api_access_token(token_str)
                            finally:
                                if db:
                                    db.close()

                        is_valid = await run_in_threadpool(
                            _verify_access_with_cleanup, access_token
                        )
                        if not is_valid:
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
                    import utils.logger as logger

                    auth = api.get_auth()
                    if auth:
                        try:
                            # SECURITY: Use hardened utility to extract real client IP from trusted proxies
                            from src.utils.net import get_client_ip
                            ip = get_client_ip(request)
                            ua = request.headers.get("User-Agent")

                            # Use a wrapper to ensure DB connection is returned to pool in the worker thread
                            def _verify_with_cleanup(token_str, client_ip, user_agent):
                                db = api.get_db()
                                try:
                                    return auth.verify_token(
                                        token_str, 
                                        ip_address=client_ip, 
                                        user_agent=user_agent
                                    )
                                finally:
                                    if db:
                                        db.close()

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
                    import utils.logger as logger
                    from utils.logger import mask_string

                    masked_header = mask_string(auth_header)
                    logger.debug(
                        f"Auth: Failed to extract token from header '{masked_header}' for path {path}"
                    )

        await self.app(scope, receive, send)

    def _extract_token(self, auth_header: str) -> Optional[str]:
        match = re.fullmatch(r"(Bearer|Bot) ([A-Za-z0-9._-]+)", auth_header)
        if match:
            return match.group(2)
        return None


async def get_current_user(request: Request) -> TokenInfo:
    """Dependency for mandatory authentication."""

    user = request.scope.get("state", {}).get("user")

    if not user:
        # Check if there was a system error (DB failed) vs auth error (invalid token)
        system_error = request.scope.get("state", {}).get("system_error")
        if system_error:
            import utils.logger as logger

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

        import utils.logger as logger

        logger.debug(
            f"get_current_user: No user in state for path {request.url.path}. Error: {error}"
        )

        raise HTTPException(
            status_code=401,
            detail={"error": {"code": 401, "message": error}},
        )

    # Enforce account status (Locked or Forced Username Change)
    path = request.url.path

    # Account Lock check (Total block)
    if user.account_locked:
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

    # Forced Username Change check
    if user.force_username_change:
        # Allow GET @me (to see current status) and PATCH @me (to fix the username)
        # Also allow logout
        is_me_path = path == "/api/v1/users/@me"
        is_allowed_me_method = request.method in ("GET", "PATCH")

        allowed = (
            (is_me_path and is_allowed_me_method)
            or path == "/api/v1/auth/logout"
            or path.startswith("/api/v1/avatars/")
        )

        if not allowed:
            raise HTTPException(
                status_code=403,
                detail={
                    "error": {
                        "code": 403,
                        "message": "Username change required",
                        "reason": "You must change your username before you can continue using PlexiChat.",
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
    except Exception:
        return None


async def get_optional_user(request: Request) -> Optional[TokenInfo]:
    """Dependency for optional authentication."""

    return request.scope.get("state", {}).get("user")
