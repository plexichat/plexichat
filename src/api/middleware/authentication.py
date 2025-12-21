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
    """ASGI middleware for token validation."""

    def __init__(self, app: ASGIApp):
        import sys
        if "pytest" in sys.modules:
            print("[DEBUG] AuthMiddleware: initialized")
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        import sys
        if "pytest" in sys.modules and scope["type"] == "http":
            print(f"[DEBUG] AuthMiddleware: __call__ for {scope.get('path')}")
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive)
        await self._process_auth(request)
        await self.app(scope, receive, send)

    async def _process_auth(self, request: Request) -> None:
        """Process authentication header and attach user info to request state."""
        import sys
        if "pytest" in sys.modules:
            print(f"[DEBUG] AuthMiddleware: Headers: {dict(request.headers)}")
        # Get all Authorization headers
        auth_headers = request.headers.getlist("Authorization")
        if not auth_headers:
            request.state.user = None
            return

        # Multiple Authorization headers are a security risk
        if len(auth_headers) > 1:
            import sys
            if "pytest" in sys.modules:
                print("[DEBUG] AuthMiddleware: multiple Authorization headers rejected")
            request.state.user = None
            return

        auth_header = auth_headers[0]
        token = self._extract_token(auth_header)
        if not token:
            request.state.user = None
            request.state.auth_error = "Invalid token format"
            return

        auth = api.get_auth()
        if not auth:
            import sys
            if "pytest" in sys.modules:
                print("[DEBUG] AuthMiddleware: auth module NOT available")
            request.state.user = None
            request.state.auth_error = "Authentication module not available"
            return

        try:
            ip_address: Optional[str] = request.client.host if request.client else None
            user_agent: Optional[str] = request.headers.get("User-Agent")
            token_info: TokenInfo = auth.verify_token(token, ip_address, user_agent)

            import sys
            if "pytest" in sys.modules:
                print(f"[DEBUG] AuthMiddleware: verified token for user {token_info.user_id}")
            
            # Ensure scope["state"] exists and update it directly
            if "state" not in request.scope:
                request.scope["state"] = {}
            request.scope["state"]["user"] = token_info
            request.state.user = token_info
        except Exception as e:
            import sys
            if "pytest" in sys.modules:
                print(f"[DEBUG] AuthMiddleware: verification failed: {e}")
            request.state.user = None
            request.state.auth_error = str(e)

    def _extract_token(self, auth_header: str) -> Optional[str]:
        """Extract token from Authorization header."""
        import sys
        # Use exact split to catch extra spaces if required by tests
        parts = auth_header.split(' ')
        if "pytest" in sys.modules:
            print(f"[DEBUG] AuthMiddleware: Extract parts: {parts}")
        if len(parts) != 2:
            return None

        scheme = parts[0]
        if scheme not in ("Bearer", "Bot"):
            import sys
            if "pytest" in sys.modules:
                print(f"[DEBUG] AuthMiddleware: invalid scheme case or type: {scheme}")
            return None

        return parts[1]


async def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> TokenInfo:
    """Dependency to get current authenticated user."""
    # Check for multiple Authorization headers (security risk)
    if len(request.headers.getlist("Authorization")) > 1:
        raise HTTPException(
            status_code=401,
            detail={"error": {"code": 401, "message": "Multiple Authorization headers not allowed"}},
        )

    if hasattr(request.state, "user"):
        if request.state.user:
            return request.state.user
        
        # If middleware explicitly set user to None, but Authorization header exists,
        # it means AuthenticationMiddleware already rejected it (e.g. invalid case).
        auth_headers = request.headers.getlist("Authorization")
        if auth_headers:
            error_msg = getattr(request.state, "auth_error", "Invalid authentication credentials")
            if "module not available" in error_msg:
                raise HTTPException(
                    status_code=500,
                    detail={"error": {"code": 500, "message": error_msg}},
                )
            raise HTTPException(
                status_code=401,
                detail={"error": {"code": 401, "message": error_msg}},
            )

    if not credentials:
        raise HTTPException(
            status_code=401,
            detail={"error": {"code": 401, "message": "Authentication required"}},
        )

    # If we get here, it means AuthenticationMiddleware did not run or did not set state.user.
    # We should still be strict about case.
    auth_header = request.headers.get("Authorization", "")
    if auth_header:
        parts = auth_header.split(' ')
        if len(parts) != 2 or parts[0] not in ("Bearer", "Bot"):
            raise HTTPException(
                status_code=401,
                detail={"error": {"code": 401, "message": "Invalid authentication scheme"}},
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
    # Check for multiple Authorization headers
    if len(request.headers.getlist("Authorization")) > 1:
        return None

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
