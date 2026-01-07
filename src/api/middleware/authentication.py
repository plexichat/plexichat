"""
Authentication middleware - Token validation and user extraction.
"""

from typing import Optional
from fastapi import Request, HTTPException
from fastapi.security import HTTPBearer
from starlette.types import ASGIApp, Receive, Send, Scope

import src.api as api
from src.core.auth.models import TokenInfo

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
        auth_header = request.headers.get("Authorization")
        if auth_header:
            token = self._extract_token(auth_header)
            if token:
                auth = api.get_auth()
                if auth:
                    try:
                        ip = request.client.host if request.client else None
                        ua = request.headers.get("User-Agent")
                        token_info = auth.verify_token(token, ip, ua)

                        scope["state"]["user"] = token_info
                    except Exception as e:
                        scope["state"]["auth_error"] = str(e)

        await self.app(scope, receive, send)

    def _extract_token(self, auth_header: str) -> Optional[str]:
        parts = auth_header.split(" ")
        if len(parts) == 2 and parts[0] in ("Bearer", "Bot"):
            return parts[1]
        return None


async def get_current_user(request: Request) -> TokenInfo:
    """Dependency for mandatory authentication."""

    user = request.scope.get("state", {}).get("user")

    if not user:
        error = request.scope.get("state", {}).get(
            "auth_error", "Authentication required"
        )

        raise HTTPException(status_code=401, detail={"error": {"message": error}})

    return user


async def get_optional_user(request: Request) -> Optional[TokenInfo]:
    """Dependency for optional authentication."""

    return request.scope.get("state", {}).get("user")
