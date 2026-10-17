"""
Base manager class for all core modules.

Also exposes shared helpers (``timestamp_ms`` / ``generate_id``) so that
both :class:`BaseManager` subclasses and the handful of standalone managers
that predate the base class derive their timestamps and Snowflake IDs from a
single source of truth instead of re-implementing ``int(time.time() * 1000)``
and ``generate_snowflake_id()`` in each module.
"""

import time
import logging
from typing import TypeAlias
from src.utils.encryption import generate_snowflake_id

# Type alias for Snowflake IDs to ensure consistent typing throughout the core layer.
# While stored as integers in the database, they are represented as strings in the API layer.
SnowflakeID: TypeAlias = int


logger = logging.getLogger(__name__)


def timestamp_ms() -> int:
    """Current Unix timestamp in milliseconds."""
    return int(time.time() * 1000)


def generate_id() -> SnowflakeID:
    """Generate a new Snowflake ID."""
    return generate_snowflake_id()


class BaseManager:
    """
    Base class for core managers providing shared utility methods.
    """

    # Canonical name of the users table used by the ``_user_exists`` fallback.
    # Kept as a class constant so subclasses and callers can reference the
    # table without repeating the literal throughout the codebase.
    _USERS_TABLE = "auth_users"

    def __init__(self, db, auth_module=None):
        """
        Initialize the base manager.

        Args:
            db: Database instance.
            auth_module: Optional auth_module for user verification.
        """
        self._db = db
        self._auth = auth_module

    @property
    def db(self):
        """Get the database instance."""
        return self._db

    def _get_timestamp(self) -> int:
        """Get current timestamp in milliseconds."""
        return timestamp_ms()

    def _generate_id(self) -> SnowflakeID:
        """Generate a new Snowflake ID."""
        return generate_id()

    def _user_exists(self, user_id: SnowflakeID) -> bool:
        """
        Check if a user exists.

        Args:
            user_id: The ID of the user to check.

        Returns:
            bool: True if user exists, False otherwise.
        """
        if self._auth:
            # If auth module is available, use its more comprehensive check
            try:
                user = self._auth.get_user(user_id)
                return user is not None
            except Exception as e:
                logger.debug(f"Auth module check failed for user {user_id}: {e}")

        # Fallback to direct DB check if auth module is not available or fails
        row = self._db.fetch_one(
            f"SELECT 1 FROM {self._USERS_TABLE} WHERE id = ?", (user_id,)
        )
        return row is not None


__all__ = [
    "SnowflakeID",
    "timestamp_ms",
    "generate_id",
    "BaseManager",
]
