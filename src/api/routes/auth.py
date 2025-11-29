"""
Authentication routes - Register, login, logout endpoints.
"""

from typing import Optional
from fastapi import APIRouter, Request, HTTPException, Depends

import src.api as api
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.auth import (
    RegisterRequest,
    LoginRequest,
    LoginResponse,
    TwoFactorRequest,
    UserResponse,
)

router = APIRouter()


def _user_to_response(user) -> UserResponse:
    """Convert user object to response model."""
    return UserResponse(
        id=str(user.id),
        username=user.username,
        email=getattr(user, "email", None),
        avatar_url=getattr(user, "avatar_url", None),
        created_at=user.created_at,
        email_verified=getattr(user, "email_verified", False),
        totp_enabled=getattr(user, "totp_enabled", False),
    )


@router.post("/register", response_model=LoginResponse)
async def register(request: Request, body: RegisterRequest):
    """
    Register a new user account.
    
    Creates a new user with the provided credentials and returns a session token.
    """
    auth = api.get_auth()
    if not auth:
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Auth module not available"}})
    
    ip_address = request.client.host if request.client else None
    
    try:
        user = auth.register(
            username=body.username,
            email=body.email,
            password=body.password,
            ip_address=ip_address
        )
        
        result = auth.login(
            username=body.username,
            password=body.password,
            ip_address=ip_address
        )
        
        return LoginResponse(
            status="success",
            token=result.token,
            user=_user_to_response(result.user) if result.user else _user_to_response(user),
            challenge_token=None,
            methods=None,
            expires_in=None,
        )
    except Exception as e:
        exc_name = type(e).__name__
        if "Exists" in exc_name:
            raise HTTPException(status_code=409, detail={"error": {"code": 409, "message": str(e)}})
        elif "Invalid" in exc_name or "Weak" in exc_name:
            raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": str(e)}})
        raise


@router.post("/login", response_model=LoginResponse)
async def login(request: Request, body: LoginRequest):
    """
    Authenticate a user.
    
    Returns a session token on success, or a 2FA challenge if enabled.
    """
    auth = api.get_auth()
    if not auth:
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Auth module not available"}})
    
    ip_address = request.client.host if request.client else None
    user_agent = request.headers.get("User-Agent")
    
    try:
        result = auth.login(
            username=body.username,
            password=body.password,
            ip_address=ip_address,
            user_agent=user_agent
        )
        
        if result.status.value == "two_factor_required":
            return LoginResponse(
                status="two_factor_required",
                token=None,
                user=None,
                challenge_token=result.challenge_token,
                methods=result.methods,
                expires_in=result.expires_in,
            )
        
        return LoginResponse(
            status="success",
            token=result.token,
            user=_user_to_response(result.user) if result.user else None,
            challenge_token=None,
            methods=None,
            expires_in=None,
        )
    except Exception as e:
        exc_name = type(e).__name__
        if "Credentials" in exc_name:
            raise HTTPException(status_code=401, detail={"error": {"code": 401, "message": "Invalid credentials"}})
        elif "Locked" in exc_name:
            raise HTTPException(status_code=403, detail={"error": {"code": 403, "message": str(e)}})
        elif "Verified" in exc_name:
            raise HTTPException(status_code=403, detail={"error": {"code": 403, "message": str(e)}})
        raise


@router.post("/2fa", response_model=LoginResponse)
async def complete_2fa(body: TwoFactorRequest):
    """
    Complete two-factor authentication.
    
    Validates the 2FA code and returns a session token on success.
    """
    auth = api.get_auth()
    if not auth:
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Auth module not available"}})
    
    try:
        result = auth.complete_2fa(body.challenge_token, body.code)
        
        return LoginResponse(
            status="success",
            token=result.token,
            user=_user_to_response(result.user) if result.user else None,
            challenge_token=None,
            methods=None,
            expires_in=None,
        )
    except Exception as e:
        exc_name = type(e).__name__
        if "Invalid" in exc_name or "Expired" in exc_name:
            raise HTTPException(status_code=401, detail={"error": {"code": 401, "message": str(e)}})
        raise


@router.post("/logout")
async def logout(current_user: TokenInfo = Depends(get_current_user)):
    """
    Logout current session.
    
    Revokes the current session token.
    """
    auth = api.get_auth()
    if not auth:
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Auth module not available"}})
    
    if current_user.session_id:
        try:
            auth.revoke_session(current_user.user_id, current_user.session_id)
        except Exception:
            pass
    
    return {"success": True}
