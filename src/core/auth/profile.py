"""
User profile management module.

This module handles updating user profile information.
"""

from typing import Optional, Dict
from .models import User
from ._lazy import _get_auth_manager


def update_user(
    user_id: int,
    username: Optional[str] = None,
    email: Optional[str] = None,
    permissions: Optional[Dict[str, bool]] = None,
) -> User:
    """
    Update user profile information.

    Args:
        user_id: ID of the user to update
        username: New username (optional)
        email: New email address (optional)
        permissions: New permissions (optional)

    Returns:
        Updated User object
    """
    return (
        _get_auth_manager()
        .get_instance()
        .update_user(user_id, username, email, permissions)
    )


__all__ = [
    "update_user",
]
