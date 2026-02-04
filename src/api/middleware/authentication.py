"""
Authentication middleware - Token validation and user extraction.
"""

from typing import Optional
from fastapi import Request, HTTPException
from fastapi.security import HTTPBearer
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
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        if "state" not in scope:
            scope["state"] = {}

        # Create request from scope ONLY to avoid consuming the 'receive' stream
        request = Request(scope)
        path = scope.get("path", "")

        # 1. Check for Internal Service Authentication
        internal_secret = api.get_internal_secret()
        # Header access remains the same
        provided_secret = request.headers.get("X-Plexichat-Internal-Secret")
        is_internal = bool(internal_secret and provided_secret == internal_secret)

        scope["state"]["is_internal"] = is_internal
        if is_internal:
            import utils.logger as logger
            logger.debug(f"Auth: Internal service authenticated for path {path}")

        # 2. Extract and Verify Bearer Token
        # Skip for admin routes which manage their own sessions, and public status/health routes
        is_admin_path = path.startswith("/admin/") or path.startswith("/api/v1/admin/")
        
        # Only skip auth for specific known public endpoints, not anything ending in /status
        public_endpoints = [
            "/api/v1/status",
            "/api/v1/health",
            "/health",
            "/status",
            "/"
        ]
        is_public_endpoint = path in public_endpoints
        
        auth_header = request.headers.get("Authorization")
        if auth_header and not is_admin_path and not is_public_endpoint:
            token = self._extract_token(auth_header)
            if token:
                import utils.logger as logger
                token_parts = token.split(".")
                is_plexichat_token = len(token_parts) >= 2
                
                auth = api.get_auth()
                if auth and is_plexichat_token:
                    try:
                        ip = request.client.host if request.client else None
                        ua = request.headers.get("User-Agent")
                        
                        # Use a wrapper to ensure DB connection is returned to pool in the worker thread
                        def _verify_with_cleanup(token_str, client_ip, user_agent):
                            db = api.get_db()
                            try:
                                return auth.verify_token(token_str, client_ip, user_agent)
                            finally:
                                if db:
                                    db.close()

                        from fastapi.concurrency import run_in_threadpool
                        token_info = await run_in_threadpool(_verify_with_cleanup, token, ip, ua)
                        
                        if token_info:
                            scope["state"]["user"] = token_info
                            logger.debug(f"Auth: Successfully authenticated user {token_info.user_id} for path {path}")
                        else:
                            logger.warning(f"Auth: verify_token returned None for path {path}")
                    except Exception as e:
                        # Distinguish between AuthError (invalid token) and other errors (DB failed)
                        if isinstance(e, AuthError):
                            # This is a legitimate authentication failure (invalid/expired token)
                            logger.debug(f"Authentication failed for path {path}: {e}")
                            scope["state"]["auth_error"] = str(e)
                        else:
                            # This is likely a database or system error
                            # We should log it as an error and potentially return a 500 instead of 401
                            logger.error(f"System error during authentication for path {path}: {e}", exc_info=True)
                            scope["state"]["system_error"] = str(e)
                elif not is_plexichat_token:
                    logger.debug(f"Auth: Identified potential admin token for path {path}")
                    scope["state"]["potential_admin_token"] = token
                elif not auth:
                    logger.error(f"Auth: Auth module NOT AVAILABLE for path {path}")
            else:
                import utils.logger as logger
                from utils.logger import mask_string
                masked_header = mask_string(auth_header)
                logger.debug(f"Auth: Failed to extract token from header '{masked_header}' for path {path}")

        await self.app(scope, receive, send)

    def _extract_token(self, auth_header: str) -> Optional[str]:
        # Split by any whitespace and handle case-insensitivity
        parts = auth_header.split()
        if len(parts) == 2 and parts[0].title() in ("Bearer", "Bot"):
            return parts[1]
        return None


async def get_current_user(request: Request) -> TokenInfo:
    """Dependency for mandatory authentication."""

    user = request.scope.get("state", {}).get("user")

    if not user:
        # Check if there was a system error (DB failed) vs auth error (invalid token)
        system_error = request.scope.get("state", {}).get("system_error")
        if system_error:
            import utils.logger as logger
            logger.error(f"get_current_user: System error blocking authentication: {system_error}")
            raise HTTPException(
                status_code=500, 
                detail={"error": {"code": 500, "message": "Internal server error during authentication"}}
            )

        error = request.scope.get("state", {}).get(
            "auth_error", "Authentication required"
        )
        
        import utils.logger as logger
        logger.debug(f"get_current_user: No user in state for path {request.url.path}. Error: {error}")

        raise HTTPException(status_code=401, detail={"error": {"message": error}})

    return user


async def get_optional_user(request: Request) -> Optional[TokenInfo]:
    """Dependency for optional authentication."""

    return request.scope.get("state", {}).get("user")
