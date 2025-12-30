"""
Base manager class for all core modules.
"""

import time
import logging
from typing import TypeAlias

# Type alias for Snowflake IDs to ensure consistent typing throughout the core layer.
# While stored as integers in the database, they are represented as strings in the API layer.
SnowflakeID: TypeAlias = int


from src.utils.encryption import generate_snowflake_id


logger = logging.getLogger(__name__)


class BaseManager:
    """
    Base class for core managers providing shared utility methods.
    """

    def __init__(self, db, auth_module=None):
        """
        Initialize the base manager.

        Args:
            db: Database instance.
            auth_module: Optional auth_module for user verification.
        """
        self._db = db
        self._auth = auth_module

    def _get_timestamp(self) -> int:
        """Get current timestamp in milliseconds."""
        return int(time.time() * 1000)

    def _generate_id(self) -> SnowflakeID:
        """Generate a new Snowflake ID."""
        return generate_snowflake_id()

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
            "SELECT 1 FROM auth_users WHERE id = ?",
            (user_id,)
        )
        return row is not None
