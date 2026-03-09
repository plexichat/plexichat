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


__all__ = [
    "get_db",
    "get_auth",
    "get_current_user",
    "get_optional_user",
]
