"""
Auth schemas - Request/response models for authentication endpoints.
"""

from typing import Optional, List
from pydantic import BaseModel, Field, EmailStr, ConfigDict

from .common import SnowflakeID


class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=32, description="Username")
    email: EmailStr = Field(..., description="Email address")
    password: str = Field(..., description="Password")
    age: Optional[int] = Field(
        None, description="User age (required if age gate enabled in boolean mode)"
    )
    age_verified: Optional[bool] = Field(
        None, description="Simple age verification check (alternative to age field)"
    )
    dob: Optional[str] = Field(
        None,
        description="User date of birth YYYY-MM-DD (required if age gate enabled in dob mode)",
    )


class LoginRequest(BaseModel):
    """User login request."""

    model_config = ConfigDict(from_attributes=True)

    username: str = Field(..., description="Username or email")
    password: str = Field(..., description="Password")


class TwoFactorRequest(BaseModel):
    """Two-factor authentication request."""

    model_config = ConfigDict(from_attributes=True)

    challenge_token: str = Field(..., description="Challenge token from login")
    code: str = Field(
        ..., min_length=6, max_length=8, description="2FA code or backup code"
    )


class UserResponse(BaseModel):
    """User information response."""

    model_config = ConfigDict(from_attributes=True)

    id: SnowflakeID = Field(..., description="User ID")
    username: str = Field(..., description="Username")
    email: Optional[str] = Field(None, description="Email (only for own user)")
    avatar_url: Optional[str] = Field(None, description="Avatar URL")
    created_at: int = Field(..., description="Account creation timestamp")
    email_verified: bool = Field(False, description="Email verification status")
    totp_enabled: bool = Field(False, description="2FA enabled status")
    age_verified: bool = Field(False, description="Age verification status")
    badges: List[str] = Field(default_factory=list, description="User badges")


class SessionResponse(BaseModel):
    """Session information response."""

    model_config = ConfigDict(from_attributes=True)

    id: SnowflakeID = Field(..., description="Session ID")
    device_id: Optional[SnowflakeID] = Field(default=None, description="Device ID")
    ip_address: Optional[str] = Field(default=None, description="IP address")
    user_agent: Optional[str] = Field(default=None, description="User agent")
    created_at: int = Field(..., description="Session creation timestamp")
    expires_at: int = Field(..., description="Session expiration timestamp")
    last_activity: int = Field(..., description="Last activity timestamp")
    current: bool = Field(False, description="Whether this is the current session")


class LoginResponse(BaseModel):
    """Login response."""

    model_config = ConfigDict(from_attributes=True)

    status: str = Field(..., description="Login status: success, two_factor_required")
    token: Optional[str] = Field(default=None, description="Session token (if success)")
    user: Optional[UserResponse] = Field(
        default=None, description="User info (if success)"
    )
    challenge_token: Optional[str] = Field(
        default=None, description="2FA challenge token"
    )
    methods: Optional[List[str]] = Field(
        default=None, description="Available 2FA methods"
    )
    expires_in: Optional[int] = Field(
        default=None, description="Challenge expiration seconds"
    )


class TokenResponse(BaseModel):
    """Token validation response."""

    model_config = ConfigDict(from_attributes=True)

    valid: bool = Field(..., description="Token validity")
    user_id: Optional[SnowflakeID] = Field(None, description="User ID")
    token_type: Optional[str] = Field(None, description="Token type: user, bot")
    expires_at: Optional[int] = Field(None, description="Token expiration timestamp")


class TwoFactorStatusResponse(BaseModel):
    """Two-factor authentication status response."""

    model_config = ConfigDict(from_attributes=True)

    enabled: bool = Field(..., description="Whether 2FA is enabled")
    backup_codes_remaining: int = Field(
        ..., description="Number of backup codes remaining"
    )


class TwoFactorSetupRequest(BaseModel):
    """Request to setup 2FA."""

    model_config = ConfigDict(from_attributes=True)

    password: str = Field(..., description="Current password for confirmation")


class TwoFactorSetupResponse(BaseModel):
    """Setup data for 2FA."""

    model_config = ConfigDict(from_attributes=True)

    secret: str = Field(..., description="2FA secret key")
    qr_uri: str = Field(..., description="QR code URI for authenticator app")
    backup_codes: List[str] = Field(default_factory=list, description="Backup codes")


class TwoFactorConfirmRequest(BaseModel):
    """Request to confirm 2FA setup."""

    model_config = ConfigDict(from_attributes=True)

    code: str = Field(
        ..., min_length=6, max_length=6, description="TOTP code from authenticator app"
    )


class TwoFactorDisableRequest(BaseModel):
    """Request to disable 2FA."""

    model_config = ConfigDict(from_attributes=True)

    password: str = Field(..., description="Current password")
    code: str = Field(
        ..., min_length=6, max_length=8, description="Current TOTP code or backup code"
    )


class RevokeAllSessionsRequest(BaseModel):
    """Request to revoke all sessions."""

    model_config = ConfigDict(from_attributes=True)

    except_current: bool = Field(
        True, description="Whether to keep the current session"
    )


class RevokeAllSessionsResponse(BaseModel):
    """Response for revoking all sessions."""

    model_config = ConfigDict(from_attributes=True)

    success: bool = Field(..., description="Whether the operation was successful")
    revoked_count: int = Field(..., description="Number of sessions revoked")


class PasswordRequirementsResponse(BaseModel):
    """Password requirements policy."""

    model_config = ConfigDict(from_attributes=True)

    min_length: int = Field(..., description="Minimum password length")
    max_length: int = Field(..., description="Maximum password length")
    require_uppercase: bool = Field(
        ..., description="Whether an uppercase letter is required"
    )
    require_lowercase: bool = Field(
        ..., description="Whether a lowercase letter is required"
    )
    require_digit: bool = Field(..., description="Whether a digit is required")
    require_special: bool = Field(
        ..., description="Whether a special character is required"
    )
    age_gate_enabled: bool = Field(False, description="Whether age gate is enabled")
    age_verification_type: str = Field(
        "boolean", description="Type of age verification: boolean or dob"
    )
    minimum_age: Optional[int] = Field(
        None, description="Minimum age required if age gate is enabled"
    )
    docs_enabled: bool = Field(
        False, description="Whether server-side documentation is enabled"
    )


class OAuthLoginResponse(BaseModel):
    """OAuth login initiation response."""

    model_config = ConfigDict(from_attributes=True)

    url: str = Field(..., description="OAuth authorization URL")
    state: str = Field(..., description="CSRF state token")
    code_verifier: Optional[str] = Field(
        default=None,
        description="PKCE code verifier (client must store and return in callback)",
    )


class PasswordResetRequest(BaseModel):
    """Request to initiate password reset."""

    model_config = ConfigDict(from_attributes=True)

    email: EmailStr = Field(..., description="Account email address")


class PasswordResetConfirm(BaseModel):
    """Request to reset password with token."""

    model_config = ConfigDict(from_attributes=True)

    token: str = Field(..., description="Reset token from email")
    new_password: str = Field(..., description="New password")


class OAuthCallbackRequest(BaseModel):
    """OAuth callback completion request."""

    model_config = ConfigDict(from_attributes=True)

    code: str = Field(..., description="OAuth authorization code")
    state: str = Field(..., description="CSRF state token")
    redirect_uri: str = Field(..., description="OAuth redirect URI")
    code_verifier: Optional[str] = Field(
        None, description="PKCE code verifier (required if PKCE was used)"
    )
