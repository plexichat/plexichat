"""
Admin authentication schemas.
"""

from typing import List, Optional
from pydantic import BaseModel, Field, ConfigDict


class AdminLoginRequest(BaseModel):
    """Admin login request."""

    model_config = ConfigDict(from_attributes=True)

    username: str = Field(..., min_length=1, max_length=100)
    password: str = Field(..., min_length=1, max_length=200)


class AdminLoginResponse(BaseModel):
    """Admin login response."""

    model_config = ConfigDict(from_attributes=True)

    status: str = Field(
        ...,
        description="Login status (success, otp_required, otp_setup_required, password_change_required)",
    )
    token: Optional[str] = Field(
        default=None, description="Session token if successful"
    )
    admin_id: Optional[str] = Field(
        default=None, description="Admin ID if OTP required"
    )
    otp_secret: Optional[str] = Field(default=None, description="OTP secret for setup")
    otp_qr_uri: Optional[str] = Field(default=None, description="OTP QR URI for setup")
    message: Optional[str] = Field(default=None, description="Instruction message")
    challenge_token: Optional[str] = Field(
        default=None, description="Short-lived challenge token for OTP verification"
    )
    requires_password_change: bool = Field(
        default=False, description="Whether admin is required to change password"
    )


class OTPVerifyRequest(BaseModel):
    """OTP verification request."""

    model_config = ConfigDict(from_attributes=True)

    admin_id: str = Field(..., description="Admin ID")
    code: str = Field(..., min_length=6, max_length=8, description="OTP code")
    is_setup: bool = Field(False, description="Whether this is for initial setup")
    challenge_token: str = Field(
        ...,
        min_length=10,
        max_length=200,
        description="Challenge token from login step",
    )


class AdminChangePasswordRequest(BaseModel):
    """Request to change admin password."""

    current_password: str = Field(...)
    new_password: str = Field(..., min_length=12)


class AdminSecurityStatusResponse(BaseModel):
    """Admin account security posture."""

    model_config = ConfigDict(from_attributes=True)

    admin_id: str
    username: str
    email: Optional[str]
    created_at: int
    last_login: Optional[int]
    otp_required: bool
    otp_enabled: bool
    must_setup_otp: bool
    backup_codes_remaining: int


class AdminOTPSetupBeginRequest(BaseModel):
    """Begin an admin OTP setup/reset flow."""

    current_password: str = Field(...)


class AdminOTPDisableRequest(BaseModel):
    """Disable admin OTP after verifying password and OTP."""

    current_password: str = Field(...)
    code: str = Field(..., min_length=6, max_length=16)


class AdminBackupCodesResponse(BaseModel):
    """Backup code regeneration response."""

    model_config = ConfigDict(from_attributes=True)

    success: bool
    backup_codes: List[str]
