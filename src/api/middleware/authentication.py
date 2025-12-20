"""
Authentication middleware - Token validation and user extraction.
"""

from typing import Optional
from fastapi import Request, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from starlette.types import ASGIApp, Receive, Send, Scope

import src.api as api
from src.core.auth.models import TokenInfo


security = HTTPBearer(auto_error=False)


class AuthenticationMiddleware:
    """Middleware for token validation."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            request = Request(scope, receive)
            await self._process_auth(request)
        await self.app(scope, receive, send)

    async def _process_auth(self, request: Request) -> None:
        """Process authentication header and attach user info to request state."""
        auth_header = request.headers.get("Authorization")
        if not auth_header:
            request.state.user = None
            return

        token = self._extract_token(auth_header)
        if not token:
            request.state.user = None
            return

        auth = api.get_auth()
        if not auth:
            request.state.user = None
            return

        try:
            ip_address: Optional[str] = request.client.host if request.client else None
            user_agent: Optional[str] = request.headers.get("User-Agent")
            token_info: TokenInfo = auth.verify_token(token, ip_address, user_agent)

            request.state.user = token_info
        except Exception:
            request.state.user = None

    def _extract_token(self, auth_header: str) -> Optional[str]:
        """Extract token from Authorization header."""
        parts = auth_header.split()
        if len(parts) != 2:
            return None

        scheme = parts[0].lower()
        if scheme not in ("bearer", "bot"):
            return None

        return parts[1]


async def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> TokenInfo:
    """Dependency to get current authenticated user."""
    if hasattr(request.state, "user") and request.state.user:
        user: TokenInfo = request.state.user
        return user

    if not credentials:
        raise HTTPException(
            status_code=401,
            detail={"error": {"code": 401, "message": "Authentication required"}},
        )

    auth = api.get_auth()
    if not auth:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Auth module not available"}},
        )

    try:
        ip_address: Optional[str] = request.client.host if request.client else None
        user_agent: Optional[str] = request.headers.get("User-Agent")
        token_info: TokenInfo = auth.verify_token(
            credentials.credentials, ip_address, user_agent
        )

        request.state.user = token_info
        return token_info
    except Exception as e:
        error_msg = str(e)
        if "expired" in error_msg.lower():
            raise HTTPException(
                status_code=401,
                detail={"error": {"code": 401, "message": "Token expired"}},
            )
        elif "revoked" in error_msg.lower():
            raise HTTPException(
                status_code=401,
                detail={"error": {"code": 401, "message": "Token revoked"}},
            )
        else:
            raise HTTPException(
                status_code=401,
                detail={"error": {"code": 401, "message": "Invalid token"}},
            )


async def get_optional_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> Optional[TokenInfo]:
    """Dependency to get current user if authenticated, None otherwise."""
    if hasattr(request.state, "user") and request.state.user:
        user: TokenInfo = request.state.user
        return user

    if not credentials:
        return None

    auth = api.get_auth()
    if not auth:
        return None

    try:
        ip_address: Optional[str] = request.client.host if request.client else None
        user_agent: Optional[str] = request.headers.get("User-Agent")
        token_info: TokenInfo = auth.verify_token(
            credentials.credentials, ip_address, user_agent
        )

        request.state.user = token_info
        return token_info
    except Exception:
        return None
