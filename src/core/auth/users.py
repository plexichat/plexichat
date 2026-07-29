"""
User management module.

This module handles user retrieval, bulk operations, and permission granting.
"""

from typing import Optional, List, Dict, Any
from .models import User
from ._lazy import _get_auth_manager


def get_user(user_id: int) -> Optional[User]:
    """Get a user by ID."""
    return _get_auth_manager().get_instance().get_user(user_id)


def get_user_by_username(username: str) -> Optional[User]:
    """Get a user by username."""
    return _get_auth_manager().get_instance().get_user_by_username(username)


def get_users_bulk(user_ids: List[int]) -> Dict[int, User]:
    """Get multiple users by ID in a single query."""
    return _get_auth_manager().get_instance().get_users_bulk(user_ids)


def get_user_profiles_bulk(user_ids: List[int]) -> Dict[str, Any]:
    """Get multiple user profiles in a single query (cached)."""
    return _get_auth_manager().get_instance().get_user_profiles_bulk(user_ids)


def grant_permission(user_id: int, permission: str) -> bool:
    """Grant a specific permission to a user."""
    return _get_auth_manager().get_instance().grant_permission(user_id, permission)


__all__ = [
    "get_user",
    "get_user_by_username",
    "get_users_bulk",
    "get_user_profiles_bulk",
    "grant_permission",
]
