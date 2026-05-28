"""
Admin authentication routes.
"""

from fastapi import APIRouter, HTTPException, Request
from src.api.schemas.admin import (
    AdminLoginRequest,
    AdminLoginResponse,
    OTPVerifyRequest,
    AdminChangePasswordRequest,
    AdminSecurityStatusResponse,
    AdminOTPSetupBeginRequest,
    AdminOTPDisableRequest,
    AdminBackupCodesResponse,
)
from src.api.schemas.common import SuccessResponse
from .utils import (
    check_host_restriction,
    get_admin_from_token,
    require_admin_permission,
)
import utils.logger as logger

router = APIRouter()


@router.post("/login", response_model=AdminLoginResponse)
async def admin_login(request: Request, login_data: AdminLoginRequest):
    """
    Authenticate an administrator.

    Performs multi-step authentication including password verification and
    optional 2FA (TOTP) setup or verification based on server configuration.
    """
    check_host_restriction(request)
    from src.core import admin

    try:
        client_ip = request.client.host if request.client else "unknown"
        result = admin.login(login_data.username, login_data.password, client_ip)
        if not result.success:
            status_code = 429 if result.rate_limited else 401
            raise HTTPException(
                status_code=status_code,
                detail={"error": {"code": status_code, "message": result.error}},
            )
        if result.token:
            response = AdminLoginResponse(status="success", token=result.token)
            if result.requires_password_change:
                response.requires_password_change = True
                response.message = "Password change required"
            return response
        if result.requires_otp_setup:
            return AdminLoginResponse(
                status="otp_setup_required",
                admin_id=str(result.user_id),
                otp_secret=result.otp_secret,
                otp_qr_uri=result.otp_qr_uri,
                message="OTP setup required",
                challenge_token=result.challenge_token,
            )
        if result.requires_otp_verify:
            return AdminLoginResponse(
                status="otp_required",
                admin_id=str(result.user_id),
                message="OTP required",
                challenge_token=result.challenge_token,
            )
        return AdminLoginResponse(status="success", token=result.token)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Login error: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.post("/verify-otp", response_model=AdminLoginResponse)
async def verify_otp(request: Request, otp_data: OTPVerifyRequest):
    """
    Verify a TOTP code for admin login or setup.

    Validates the provided code against the challenge token to complete authentication.
    """
    check_host_restriction(request)
    from src.core import admin

    try:
        admin_id = int(otp_data.admin_id)
        result = (
            admin.verify_otp_setup(admin_id, otp_data.code, otp_data.challenge_token)
            if otp_data.is_setup
            else admin.verify_otp(admin_id, otp_data.code, otp_data.challenge_token)
        )
        if not result.success:
            raise HTTPException(
                status_code=401,
                detail={"error": {"code": 401, "message": result.error}},
            )

        response = AdminLoginResponse(status="success", token=result.token)
        if result.requires_password_change:
            response.requires_password_change = True
            response.message = "Password change required"

        return response
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"OTP error: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.post("/logout", response_model=SuccessResponse)
async def admin_logout(request: Request):
    """
    Invalidate an administrator session.

    Revokes the current access token and clears associated session data.
    """
    check_host_restriction(request)
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        from src.core import admin

        admin.logout(auth_header[7:])
    return SuccessResponse(success=True, message="Logged out successfully")


@router.post("/auth/change-password", response_model=SuccessResponse)
async def admin_change_password(request: Request, body: AdminChangePasswordRequest):
    """
    Change the password for the current administrator.

    Requires verification of the current password before applying the new one.
    """
    check_host_restriction(request)
    admin_id = get_admin_from_token(request)
    from src.core import admin

    success, message = admin.change_password(
        admin_id, body.current_password, body.new_password
    )
    if not success:
        raise HTTPException(
            status_code=400, detail={"error": {"code": 400, "message": message}}
        )
    return SuccessResponse(success=True, message="Password changed successfully")


@router.post(
    "/auth/force-password-change/{target_admin_id}", response_model=SuccessResponse
)
async def admin_force_password_change(request: Request, target_admin_id: str):
    """
    Force a specific admin to change their password on next login.

    Requires admin.edit permission.
    """
    check_host_restriction(request)
    current_admin_id = require_admin_permission(request, "admin.edit")

    try:
        target_id = int(target_admin_id)
        import src.api as api

        db = api.get_db()
        if db is None:
            raise HTTPException(
                status_code=500,
                detail={"error": {"code": 500, "message": "Database not available"}},
            )
        db.execute(
            "UPDATE admin_users SET force_password_change = 1 WHERE id = ?",
            (target_id,),
        )

        # Log the action
        from src.core.admin.permissions import log_admin_action

        log_admin_action(
            db,
            current_admin_id,
            "force_password_change",
            "admin_user",
            target_id,
            {"message": f"Forced password change for admin {target_id}"},
            request.client.host if request.client else "unknown",
        )

        return SuccessResponse(
            success=True, message="Password change forced successfully"
        )
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Invalid admin ID"}},
        )


@router.get("/auth/security-status", response_model=AdminSecurityStatusResponse)
async def admin_security_status(request: Request):
    """Return the current admin account security settings and posture."""
    check_host_restriction(request)
    admin_id = get_admin_from_token(request)
    from src.core import admin

    status = admin.get_security_status(admin_id)
    if not status:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": 404, "message": "Admin user not found"}},
        )
    return AdminSecurityStatusResponse(
        admin_id=str(status.admin_id),
        username=status.username,
        email=status.email,
        created_at=status.created_at,
        last_login=status.last_login,
        otp_required=status.otp_required,
        otp_enabled=status.otp_enabled,
        must_setup_otp=status.must_setup_otp,
        backup_codes_remaining=status.backup_codes_remaining,
    )


@router.post("/auth/2fa/begin-setup", response_model=AdminLoginResponse)
async def admin_begin_otp_setup(request: Request, body: AdminOTPSetupBeginRequest):
    """Start a new OTP setup flow for the current admin account."""
    check_host_restriction(request)
    admin_id = get_admin_from_token(request)
    from src.core import admin

    result = admin.begin_otp_setup(admin_id, body.current_password)
    if not result.success:
        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "code": 400,
                    "message": result.error or "Failed to begin OTP setup",
                }
            },
        )
    return AdminLoginResponse(
        status="otp_setup_required",
        admin_id=str(result.user_id),
        otp_secret=result.otp_secret,
        otp_qr_uri=result.otp_qr_uri,
        challenge_token=result.challenge_token,
        message="OTP setup started",
    )


@router.post("/auth/2fa/disable", response_model=SuccessResponse)
async def admin_disable_otp(request: Request, body: AdminOTPDisableRequest):
    """Disable OTP for the current admin after password and OTP verification."""
    check_host_restriction(request)
    admin_id = get_admin_from_token(request)
    from src.core import admin

    success, message = admin.disable_otp(admin_id, body.current_password, body.code)
    if not success:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": message}},
        )
    return SuccessResponse(success=True, message="OTP disabled successfully")


@router.post(
    "/auth/2fa/regenerate-backup-codes", response_model=AdminBackupCodesResponse
)
async def admin_regenerate_backup_codes(
    request: Request, body: AdminOTPSetupBeginRequest
):
    """Regenerate backup codes for the current admin."""
    check_host_restriction(request)
    admin_id = get_admin_from_token(request)
    from src.core import admin

    success, backup_codes, message = admin.regenerate_backup_codes(
        admin_id, body.current_password
    )
    if not success:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": message}},
        )
    return AdminBackupCodesResponse(success=True, backup_codes=backup_codes)
