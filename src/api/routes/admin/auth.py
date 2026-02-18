"""
Admin authentication routes.
"""

from fastapi import APIRouter, HTTPException, Request
from src.api.schemas.admin import (
    AdminLoginRequest,
    AdminLoginResponse,
    OTPVerifyRequest,
    AdminChangePasswordRequest,
)
from src.api.schemas.common import SuccessResponse
from .utils import check_host_restriction, get_admin_from_token
import utils.logger as logger

router = APIRouter()


@router.post("/login", response_model=AdminLoginResponse)
async def admin_login(request: Request, login_data: AdminLoginRequest):
    check_host_restriction(request)
    from src.core import admin

    try:
        client_ip = request.client.host if request.client else "unknown"
        result = admin.login(login_data.username, login_data.password, client_ip)
        if not result.success:
            raise HTTPException(
                status_code=401,
                detail={"error": {"code": 401, "message": result.error}},
            )
        if result.token:
            return AdminLoginResponse(status="success", token=result.token)
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
    except Exception as e:
        if isinstance(e, HTTPException):
            raise
        logger.error(f"Login error: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )


@router.post("/verify-otp", response_model=AdminLoginResponse)
async def verify_otp(request: Request, otp_data: OTPVerifyRequest):
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
        return AdminLoginResponse(status="success", token=result.token)
    except Exception as e:
        if isinstance(e, HTTPException):
            raise
        logger.error(f"OTP error: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )


@router.post("/logout", response_model=SuccessResponse)
async def admin_logout(request: Request):
    check_host_restriction(request)
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        from src.core import admin

        admin.logout(auth_header[7:])
    return SuccessResponse(success=True)


@router.post("/auth/change-password", response_model=SuccessResponse)
async def admin_change_password(request: Request, body: AdminChangePasswordRequest):
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
    return SuccessResponse(success=True)
