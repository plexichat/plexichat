"""
User registration module.

This module handles user registration, email verification, and verification resending.
"""

from typing import Optional, Dict
from .models import User
from ._lazy import _get_auth_manager


def register(
    username: str,
    email: str,
    password: str,
    device_info: Optional[Dict[str, str]] = None,
    ip_address: Optional[str] = None,
    age: Optional[int] = None,
    dob: Optional[str] = None,
    is_internal: bool = False,
) -> User:
    """
    Register a new user account.

    Args:
        username: Unique username
        email: Email address
        password: Password (will be validated for strength)
        device_info: Optional device information
        ip_address: Optional IP address
        age: Optional user age
        dob: Optional date of birth
        is_internal: Whether this is an internal request (bypasses blacklist)

    Returns:
        Created User object

    Raises:
        UserExistsError: Username or email already taken
        WeakPasswordError: Password does not meet requirements
        InvalidUsernameError: Username format invalid
        InvalidEmailError: Email format invalid
    """
    return (
        _get_auth_manager()
        .get_instance()
        .register(
            username, email, password, device_info, ip_address, age, dob, is_internal
        )
    )


def register_selftest(
    username: str,
    email: str,
    password: str,
    device_info: Optional[Dict[str, str]] = None,
    ip_address: Optional[str] = None,
    age: Optional[int] = None,
    dob: Optional[str] = None,
) -> User:
    """
    Register a new user account for internal use (e.g. self-test).

    Bypasses the username blacklist check so internal accounts can use
    reserved names like 'selftest_admin' without conflicting with the
    blacklist pattern '^selftest' that prevents real users from taking them.

    Args:
        username: Unique username
        email: Email address
        password: Password (will be validated for strength)
        device_info: Optional device information
        ip_address: Optional IP address
        age: Optional user age
        dob: Optional date of birth

    Returns:
        Created User object

    Raises:
        UserExistsError: Username or email already taken
        WeakPasswordError: Password does not meet requirements
        InvalidUsernameError: Username format invalid
        InvalidEmailError: Email format invalid
    """
    return (
        _get_auth_manager()
        .get_instance()
        .register(
            username,
            email,
            password,
            device_info,
            ip_address,
            age,
            dob,
            is_internal=True,
        )
    )


def verify_email(token: str) -> bool:
    """Verify email address with token from verification email."""
    return _get_auth_manager().get_instance().verify_email(token)


def resend_verification(email: str) -> bool:
    """Resend email verification. Returns False if email not configured."""
    return _get_auth_manager().get_instance().resend_verification(email)


__all__ = [
    "register",
    "register_selftest",
    "verify_email",
    "resend_verification",
]
