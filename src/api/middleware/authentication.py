"""
Authentication middleware - Token validation and user extraction.
"""

from typing import Optional
from fastapi import Request, HTTPException
from fastapi.security import HTTPBearer
from starlette.types import ASGIApp, Receive, Send, Scope

import src.api as api
from src.core.auth.models import TokenInfo, AccountType

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

        request = Request(scope, receive)

        # 1. Check for Internal Service Authentication (No magic bypasses)
        internal_secret = api.get_internal_secret()
        provided_secret = request.headers.get("X-Plexichat-Internal-Secret")
        is_internal = internal_secret and provided_secret == internal_secret

        scope["state"]["is_internal"] = is_internal

        # 2. Extract and Verify Bearer Token
        # Skip for admin routes which manage their own sessions
        path = scope.get("path", "")
        is_admin_path = path.startswith("/admin/") or path.startswith("/api/v1/admin/")
        
        auth_header = request.headers.get("Authorization")
        if auth_header and not is_admin_path:
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
                        token_info = auth.verify_token(token, ip, ua)
                        if token_info:
                            scope["state"]["user"] = token_info
                            logger.debug(f"Auth: Successfully authenticated user {token_info.user_id} for path {path}")
                        else:
                            logger.warning(f"Auth: verify_token returned None for path {path}")
                    except Exception as e:
                        # Only log legitimate auth errors, not format mismatches
                        logger.error(f"Authentication failed for path {path}: {e}", exc_info=True)
                        scope["state"]["auth_error"] = str(e)
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
