"""
User login module.

This module handles user authentication including username/password login
and two-factor authentication completion.
"""

from typing import Optional, Dict
from .models import AuthResult
from ._lazy import _get_auth_manager


def login(
    username: str,
    password: str,
    device_info: Optional[Dict[str, str]] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> AuthResult:
    """
    Authenticate a user.

    Args:
        username: Username or email
        password: Password
        device_info: Optional device information
        ip_address: Optional IP address
        user_agent: Optional user agent string

    Returns:
        AuthResult with status and token/challenge

    Raises:
        InvalidCredentialsError: Wrong username or password
        AccountLockedError: Account temporarily locked
        AccountDisabledError: Account permanently disabled
        EmailNotVerifiedError: Email verification required
    """
    return (
        _get_auth_manager()
        .get_instance()
        .login(username, password, device_info, ip_address, user_agent)
    )


def complete_2fa(challenge_token: str, code: str) -> AuthResult:
    """Complete 2FA challenge with TOTP code or backup code."""
    return _get_auth_manager().get_instance().complete_2fa(challenge_token, code)


__all__ = [
    "login",
    "complete_2fa",
]
