"""
Authentication routes - Register, login, logout endpoints.
"""

import os
import sys
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
from src.api.schemas.common import SnowflakeID

# Import config utility
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.."))
common_utils_path = os.path.join(project_root, "src", "utils", "common-utils")
for path in [project_root, common_utils_path]:
    if path not in sys.path:
        sys.path.insert(0, path)

try:
    import utils.config as config_util
except ImportError:
    config_util = None

router = APIRouter()


def _user_to_response(user) -> UserResponse:
    """Convert user object to response model."""
    return UserResponse(
        id=SnowflakeID(user.id),
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
    If alpha registration mode is enabled, automatically grants alpha tier and badge.
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

        # Apply alpha tester features if enabled
        features = api.get_features()
        if features:
            try:
                features.apply_new_user_features(user.id)
            except Exception:
                pass  # Non-critical, don't fail registration

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


@router.get("/2fa/status")
async def get_2fa_status(current_user: TokenInfo = Depends(get_current_user)):
    """
    Get current 2FA status for the user.
    """
    auth = api.get_auth()
    if not auth:
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Auth module not available"}})

    try:
        status = auth.get_2fa_status(current_user.user_id)
        return {
            "enabled": status.enabled,
            "backup_codes_remaining": status.backup_codes_remaining
        }
    except Exception as e:
        if "NotFound" in type(e).__name__:
            raise HTTPException(status_code=404, detail={"error": {"code": 404, "message": "User not found"}})
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": str(e)}})


@router.get("/sessions")
async def get_sessions_list(current_user: TokenInfo = Depends(get_current_user)):
    """
    Get all active sessions for the current user.
    """
    auth = api.get_auth()
    if not auth:
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Auth module not available"}})

    try:
        sessions = auth.get_sessions(current_user.user_id)
        return [
            {
                "id": str(s.id),
                "ip_address": getattr(s, "ip_address", None),
                "user_agent": getattr(s, "user_agent", None),
                "created_at": getattr(s, "created_at", None),
                "last_activity": getattr(s, "last_activity", None),
                "current": s.id == current_user.session_id
            }
            for s in sessions
        ]
    except Exception as e:
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": str(e)}})


@router.delete("/sessions/{session_id}")
async def revoke_session(session_id: str, current_user: TokenInfo = Depends(get_current_user)):
    """
    Revoke a specific session.
    """
    auth = api.get_auth()
    if not auth:
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Auth module not available"}})

    try:
        sid = int(session_id)
        auth.revoke_session(current_user.user_id, sid)
        return {"success": True}
    except ValueError:
        raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": "Invalid session ID"}})
    except Exception as e:
        exc_name = type(e).__name__
        if "NotFound" in exc_name:
            raise HTTPException(status_code=404, detail={"error": {"code": 404, "message": "Session not found"}})
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": str(e)}})


@router.post("/2fa/enable")
async def enable_2fa(body: dict, current_user: TokenInfo = Depends(get_current_user)):
    """
    Enable 2FA - returns QR code and secret.
    
    Requires password confirmation. Returns setup data for authenticator app.
    """
    auth = api.get_auth()
    if not auth:
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Auth module not available"}})

    password = body.get("password", "")
    if not password:
        raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": "Password required"}})

    try:
        user = auth.get_user(current_user.user_id)
        if not user:
            raise HTTPException(status_code=404, detail={"error": {"code": 404, "message": "User not found"}})

        # Check if already enabled
        if getattr(user, "totp_enabled", False):
            raise HTTPException(status_code=409, detail={"error": {"code": 409, "message": "2FA is already enabled"}})

        # Verify password by attempting a login (this validates credentials)
        try:
            auth.login(user.username, password)
        except Exception as login_err:
            if "Credentials" in type(login_err).__name__ or "Invalid" in type(login_err).__name__:
                raise HTTPException(status_code=401, detail={"error": {"code": 401, "message": "Invalid password"}})
            # If it's a 2FA required error, password was correct
            if "TwoFactor" not in type(login_err).__name__:
                raise

        # Setup 2FA - returns TwoFactorSetup object
        result = auth.setup_2fa(current_user.user_id)
        return {
            "secret": result.secret,
            "qr_uri": result.qr_uri,
            "backup_codes": result.backup_codes or []
        }
    except HTTPException:
        raise
    except Exception as e:
        exc_name = type(e).__name__
        if "Invalid" in exc_name or "Credentials" in exc_name:
            raise HTTPException(status_code=401, detail={"error": {"code": 401, "message": "Invalid password"}})
        elif "Already" in exc_name or "Enabled" in exc_name:
            raise HTTPException(status_code=409, detail={"error": {"code": 409, "message": "2FA is already enabled"}})
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": str(e)}})


@router.post("/2fa/confirm")
async def confirm_2fa_setup(body: dict, current_user: TokenInfo = Depends(get_current_user)):
    """
    Confirm 2FA setup with TOTP code.
    
    Validates the code from authenticator app to complete 2FA setup.
    """
    auth = api.get_auth()
    if not auth:
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Auth module not available"}})

    code = body.get("code", "")
    if not code or len(code) != 6:
        raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": "Valid 6-digit code required"}})

    try:
        # confirm_2fa returns bool
        success = auth.confirm_2fa(current_user.user_id, code)
        if success:
            return {"success": True}
        else:
            raise HTTPException(status_code=401, detail={"error": {"code": 401, "message": "Invalid code"}})
    except HTTPException:
        raise
    except Exception as e:
        exc_name = type(e).__name__
        if "Invalid" in exc_name:
            raise HTTPException(status_code=401, detail={"error": {"code": 401, "message": "Invalid code"}})
        elif "NotFound" in exc_name or "Setup" in exc_name:
            raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": "2FA setup not started"}})
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": str(e)}})


@router.post("/2fa/disable")
async def disable_2fa(body: dict, current_user: TokenInfo = Depends(get_current_user)):
    """
    Disable 2FA.
    
    Requires password and current 2FA code for security.
    """
    auth = api.get_auth()
    if not auth:
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Auth module not available"}})

    password = body.get("password", "")
    code = body.get("code", "")

    if not password:
        raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": "Password required"}})
    if not code:
        raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": "2FA code required"}})

    try:
        auth.disable_2fa(current_user.user_id, password, code)
        return {"success": True}
    except Exception as e:
        exc_name = type(e).__name__
        if "Invalid" in exc_name or "Credentials" in exc_name:
            raise HTTPException(status_code=401, detail={"error": {"code": 401, "message": "Invalid password or code"}})
        elif "NotEnabled" in exc_name or "Disabled" in exc_name:
            raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": "2FA is not enabled"}})
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": str(e)}})


@router.post("/sessions/revoke-all")
async def revoke_all_sessions(body: dict, current_user: TokenInfo = Depends(get_current_user)):
    """
    Revoke all sessions except optionally the current one.
    """
    auth = api.get_auth()
    if not auth:
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Auth module not available"}})

    except_current = body.get("except_current", True)

    try:
        sessions = auth.get_sessions(current_user.user_id)
        revoked = 0
        for s in sessions:
            if except_current and s.id == current_user.session_id:
                continue
            try:
                auth.revoke_session(current_user.user_id, s.id)
                revoked += 1
            except Exception:
                pass
        return {"success": True, "revoked_count": revoked}
    except Exception as e:
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": str(e)}})


@router.get("/password-requirements")
async def get_password_requirements():
    """
    Get server password requirements.
    
    Returns the password policy configuration so clients can validate
    passwords before submission and display requirements to users.
    """
    # Default requirements (should match main.py defaults)
    defaults = {
        "min_length": 12,
        "max_length": 128,
        "require_uppercase": True,
        "require_lowercase": True,
        "require_digit": True,
        "require_special": True,
    }

    if config_util is None:
        return defaults

    try:
        # Try to get the nested authentication.password config
        auth_config = config_util.get("authentication", {})
        password_config = auth_config.get("password", {}) if isinstance(auth_config, dict) else {}

        return {
            "min_length": password_config.get("min_length", defaults["min_length"]),
            "max_length": password_config.get("max_length", defaults["max_length"]),
            "require_uppercase": password_config.get("require_uppercase", defaults["require_uppercase"]),
            "require_lowercase": password_config.get("require_lowercase", defaults["require_lowercase"]),
            "require_digit": password_config.get("require_digit", defaults["require_digit"]),
            "require_special": password_config.get("require_special", defaults["require_special"]),
        }
    except Exception:
        return defaults
