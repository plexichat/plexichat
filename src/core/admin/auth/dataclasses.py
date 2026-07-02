"""
Dataclasses for admin authentication.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class AdminLoginResult:
    """Result of admin login attempt."""

    success: bool
    token: Optional[str] = None
    user_id: Optional[int] = None
    requires_otp_setup: bool = False
    otp_secret: Optional[str] = None
    otp_qr_uri: Optional[str] = None
    requires_otp_verify: bool = False
    challenge_token: Optional[str] = None
    error: Optional[str] = None
    rate_limited: bool = False
    requires_password_change: bool = False


@dataclass
class AdminSecurityStatus:
    """Current admin account security posture."""

    admin_id: int
    username: str
    email: Optional[str]
    created_at: int
    last_login: Optional[int]
    otp_required: bool
    otp_enabled: bool
    must_setup_otp: bool
    backup_codes_remaining: int
