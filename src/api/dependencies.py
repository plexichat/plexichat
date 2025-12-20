"""
API dependencies - Dependency injection utilities.
"""

from typing import Any, Optional

import src.api as api
from src.api.middleware.authentication import get_current_user, get_optional_user


def get_db() -> Optional[Any]:
    """Get database instance dependency."""
    return api.get_db()


def get_auth() -> Optional[Any]:
    """Get auth module dependency."""
    return api.get_auth()


def get_messaging() -> Optional[Any]:
    """Get messaging module dependency."""
    return api.get_messaging()


def get_servers() -> Optional[Any]:
    """Get servers module dependency."""
    return api.get_servers()


def get_relationships() -> Optional[Any]:
    """Get relationships module dependency."""
    return api.get_relationships()


def get_presence() -> Optional[Any]:
    """Get presence module dependency."""
    return api.get_presence()


def get_reactions() -> Optional[Any]:
    """Get reactions module dependency."""
    return api.get_reactions()


def get_embeds() -> Optional[Any]:
    """Get embeds module dependency."""
    return api.get_embeds()


def get_notifications() -> Optional[Any]:
    """Get notifications module dependency."""
    return api.get_notifications()


def get_webhooks() -> Optional[Any]:
    """Get webhooks module dependency."""
    return api.get_webhooks()


def get_threads() -> Optional[Any]:
    """Get threads module dependency."""
    return api.get_threads()


__all__ = [
    "get_db",
    "get_auth",
    "get_messaging",
    "get_servers",
    "get_relationships",
    "get_presence",
    "get_reactions",
    "get_embeds",
    "get_notifications",
    "get_webhooks",
    "get_threads",
    "get_current_user",
    "get_optional_user",
]
