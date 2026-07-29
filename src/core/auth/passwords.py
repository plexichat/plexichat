"""
Password management module.

This module handles password changes, reset requests, resets, and validation.
"""

import re
from typing import List, Tuple

import utils.config as config

from .models import PasswordValidation
from ._lazy import _get_auth_manager


def validate_username(username: str) -> Tuple[bool, List[str]]:
    """
    Validate username format.

    Args:
        username: Username to validate

    Returns:
        Tuple of (valid, issues)
    """
    auth_config = config.get("authentication", {})
    accounts_config = auth_config.get("accounts", {})

    min_length = accounts_config.get("username_min_length", 3)
    max_length = accounts_config.get("username_max_length", 32)
    pattern = accounts_config.get("username_pattern", r"^[a-zA-Z0-9_]+$")

    issues = []

    if len(username) < min_length:
        issues.append(f"Username must be at least {min_length} characters")

    if len(username) > max_length:
        issues.append(f"Username must be at most {max_length} characters")

    if not re.match(pattern, username):
        issues.append("Username can only contain letters, numbers, and underscores")

    reserved = {
        "admin",
        "administrator",
        "system",
        "bot",
        "api",
        "root",
        "null",
        "undefined",
    }
    if username.lower() in reserved:
        issues.append("This username is reserved")

    return len(issues) == 0, issues


def change_password(user_id: int, old_password: str, new_password: str) -> bool:
    """Change password. Requires current password."""
    return (
        _get_auth_manager()
        .get_instance()
        .change_password(user_id, old_password, new_password)
    )


def request_password_reset(email: str) -> bool:
    """Request password reset email. Returns False if email not configured."""
    return _get_auth_manager().get_instance().request_password_reset(email)


def reset_password(token: str, new_password: str) -> bool:
    """Reset password with token from reset email."""
    return _get_auth_manager().get_instance().reset_password(token, new_password)


def get_password_config() -> dict:
    """Get password configuration from config system."""
    auth_config = config.get("authentication", {})
    return auth_config.get(
        "password",
        {
            "min_length": 12,
            "max_length": 128,
            "require_uppercase": True,
            "require_lowercase": True,
            "require_digit": True,
            "require_special": True,
        },
    )


def validate_password(password: str) -> PasswordValidation:
    """
    Validate password strength against configured requirements.

    Args:
        password: Password to validate

    Returns:
        PasswordValidation with valid flag, score, and issues
    """
    pwd_config = get_password_config()
    issues = []
    score = 0

    min_length = pwd_config.get("min_length", 12)
    max_length = pwd_config.get("max_length", 128)

    if len(password) < min_length:
        issues.append(f"Password must be at least {min_length} characters")
    else:
        score += 1

    if len(password) > max_length:
        issues.append(f"Password must be at most {max_length} characters")

    if pwd_config.get("require_uppercase", True):
        if not re.search(r"[A-Z]", password):
            issues.append("Password must contain at least one uppercase letter")
        else:
            score += 1

    if pwd_config.get("require_lowercase", True):
        if not re.search(r"[a-z]", password):
            issues.append("Password must contain at least one lowercase letter")
        else:
            score += 1

    if pwd_config.get("require_digit", True):
        if not re.search(r"\d", password):
            issues.append("Password must contain at least one digit")
        else:
            score += 1

    if pwd_config.get("require_special", True):
        if not re.search(r"[!@#$%^&*(),.?\":{}|<>_\-+=\[\]\\;'/`~]", password):
            issues.append("Password must contain at least one special character")
        else:
            score += 1

    if len(password) >= 16:
        score += 1
    if len(password) >= 20:
        score += 1

    score = min(score, 5)

    return PasswordValidation(
        valid=len(issues) == 0,
        score=score,
        issues=issues,
    )


def validate_email(email: str) -> bool:
    """
    Validate email format.

    Args:
        email: Email to validate

    Returns:
        True if email format is valid
    """
    pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
    return bool(re.match(pattern, email))


__all__ = [
    "change_password",
    "request_password_reset",
    "reset_password",
    "validate_password",
    "validate_username",
    "validate_email",
]
