"""
Two-Factor Authentication (2FA) module.

This module handles TOTP-based two-factor authentication including
setup, confirmation, disabling, backup code regeneration, and status checking.
"""

from typing import List
from .models import TwoFactorSetup, TwoFactorStatus
from ._lazy import _get_auth_manager


def setup_2fa(user_id: int) -> TwoFactorSetup:
    """
    Begin 2FA setup. Returns secret, QR URI, and backup codes.
    User must call confirm_2fa() with a valid code to enable.
    """
    return _get_auth_manager().get_instance().setup_2fa(user_id)


def confirm_2fa(user_id: int, code: str) -> bool:
    """Confirm 2FA setup with a valid TOTP code."""
    return _get_auth_manager().get_instance().confirm_2fa(user_id, code)


def disable_2fa(user_id: int, password: str, code: str) -> bool:
    """Disable 2FA. Requires password and current TOTP code."""
    return _get_auth_manager().get_instance().disable_2fa(user_id, password, code)


def regenerate_backup_codes(user_id: int, password: str) -> List[str]:
    """Regenerate backup codes. Invalidates old codes."""
    return _get_auth_manager().get_instance().regenerate_backup_codes(user_id, password)


def get_2fa_status(user_id: int) -> TwoFactorStatus:
    """Get 2FA status for a user."""
    return _get_auth_manager().get_instance().get_2fa_status(user_id)


__all__ = [
    "setup_2fa",
    "confirm_2fa",
    "disable_2fa",
    "regenerate_backup_codes",
    "get_2fa_status",
]
