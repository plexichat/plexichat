"""
API dependencies - Dependency injection utilities.
"""

from typing import Optional
from fastapi import Depends

import src.api as api
from src.api.middleware.authentication import get_current_user, get_optional_user, TokenInfo


def get_db():
    """Get database instance dependency."""
    return api.get_db()


def get_auth():
    """Get auth module dependency."""
    return api.get_auth()


def get_messaging():
    """Get messaging module dependency."""
    return api.get_messaging()


def get_servers():
    """Get servers module dependency."""
    return api.get_servers()


def get_relationships():
    """Get relationships module dependency."""
    return api.get_relationships()


def get_presence():
    """Get presence module dependency."""
    return api.get_presence()


def get_reactions():
    """Get reactions module dependency."""
    return api.get_reactions()


def get_embeds():
    """Get embeds module dependency."""
    return api.get_embeds()


def get_notifications():
    """Get notifications module dependency."""
    return api.get_notifications()


def get_webhooks():
    """Get webhooks module dependency."""
    return api.get_webhooks()


def get_threads():
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
