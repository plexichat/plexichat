"""
Settings manager - handles CRUD operations for user settings.
"""

from typing import Optional, Dict, List

import utils.logger as logger

from src.core.base import BaseManager, SnowflakeID
from .models import UserSetting, SettingsConfig
from .exceptions import (
    SettingsLimitExceeded,
    SettingsKeyTooLong,
    SettingsValueTooLong,
    SettingsKeyReserved,
)


class SettingsManager(BaseManager):
    """
    Manages user settings with configurable limits.
    """

    def __init__(self, db, config: Optional[SettingsConfig] = None):
        """
        Initialize the settings manager.

        Args:
            db: Database instance (must be connected)
            config: Optional settings configuration
        """
        super().__init__(db)
        self.config = config or SettingsConfig()

        logger.info(
            f"Settings manager initialized (max {self.config.max_settings_per_user} settings/user)"
        )

    def _validate_key(self, key: str) -> None:
        """Validate a setting key."""
        if len(key) > self.config.max_key_length:
            raise SettingsKeyTooLong(
                f"Key exceeds maximum length of {self.config.max_key_length} characters"
            )

        for reserved in self.config.reserved_keys:
            if key.startswith(reserved):
                raise SettingsKeyReserved(
                    f"Key '{key}' uses reserved prefix '{reserved}'"
                )

    def _validate_value(self, value: str) -> None:
        """Validate a setting value."""
        if len(value) > self.config.max_value_length:
            raise SettingsValueTooLong(
                f"Value exceeds maximum length of {self.config.max_value_length} characters"
            )

    def get(self, user_id: SnowflakeID, key: str) -> Optional[str]:
        """
        Get a single setting value.

        Args:
            user_id: User ID
            key: Setting key

        Returns:
            Setting value or None if not found
        """
        row = self._db.fetch_one(
            "SELECT value FROM user_settings WHERE user_id = ? AND key = ?",
            (user_id, key),
        )

        if row:
            logger.debug(f"Retrieved setting '{key}' for user {user_id}")
            return row.get("value")
        return None

    def set(self, user_id: SnowflakeID, key: str, value: str) -> UserSetting:
        """
        Set a setting value (insert or update).

        Args:
            user_id: User ID
            key: Setting key
            value: Setting value

        Returns:
            The created/updated UserSetting

        Raises:
            SettingsLimitExceeded: If user has too many settings
            SettingsKeyTooLong: If key is too long
            SettingsValueTooLong: If value is too long
            SettingsKeyReserved: If key uses reserved prefix
        """
        self._validate_key(key)
        self._validate_value(value)

        now = self._get_timestamp()

        # Check if setting exists
        existing = self._db.fetch_one(
            "SELECT id, created_at FROM user_settings WHERE user_id = ? AND key = ?",
            (user_id, key),
        )

        if existing:
            # Update existing
            setting_id = existing.get("id")
            created_at = existing.get("created_at")
            self._db.execute(
                "UPDATE user_settings SET value = ?, updated_at = ? WHERE id = ?",
                (value, now, setting_id),
            )
            logger.info(f"Updated setting '{key}' for user {user_id}")
            return UserSetting(
                id=setting_id,
                user_id=user_id,
                key=key,
                value=value,
                created_at=created_at,
                updated_at=now,
            )

        # Check limit before inserting
        count = self.get_count(user_id)
        if count >= self.config.max_settings_per_user:
            raise SettingsLimitExceeded(
                f"User has reached maximum of {self.config.max_settings_per_user} settings"
            )

        # Insert new
        setting_id = self._generate_id()

        self._db.execute(
            """INSERT INTO user_settings (id, user_id, key, value, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (setting_id, user_id, key, value, now, now),
        )

        logger.info(f"Created setting '{key}' for user {user_id}")
        return UserSetting(
            id=setting_id,
            user_id=user_id,
            key=key,
            value=value,
            created_at=now,
            updated_at=now,
        )

    def delete(self, user_id: SnowflakeID, key: str) -> bool:
        """
        Delete a setting.

        Args:
            user_id: User ID
            key: Setting key

        Returns:
            True if deleted, False if not found
        """
        cursor = self._db.execute(
            "DELETE FROM user_settings WHERE user_id = ? AND key = ?", (user_id, key)
        )
        deleted = cursor.rowcount > 0
        cursor.close()

        if deleted:
            logger.info(f"Deleted setting '{key}' for user {user_id}")
        return deleted

    def get_all(self, user_id: SnowflakeID) -> Dict[str, str]:
        """
        Get all settings for a user as a dictionary.

        Args:
            user_id: User ID

        Returns:
            Dictionary of key-value pairs
        """
        rows = self._db.fetch_all(
            "SELECT key, value FROM user_settings WHERE user_id = ?", (user_id,)
        )

        result = {row.get("key"): row.get("value") for row in rows}
        logger.debug(f"Retrieved {len(result)} settings for user {user_id}")
        return result

    def get_all_as_list(self, user_id: SnowflakeID) -> List[UserSetting]:
        """
        Get all settings for a user as a list of UserSetting objects.

        Args:
            user_id: User ID

        Returns:
            List of UserSetting objects
        """
        rows = self._db.fetch_all(
            "SELECT id, user_id, key, value, created_at, updated_at FROM user_settings WHERE user_id = ?",
            (user_id,),
        )

        return [
            UserSetting(
                id=row.get("id"),
                user_id=row.get("user_id"),
                key=row.get("key"),
                value=row.get("value"),
                created_at=row.get("created_at"),
                updated_at=row.get("updated_at"),
            )
            for row in rows
        ]

    def get_count(self, user_id: SnowflakeID) -> int:
        """
        Get the count of settings for a user.

        Args:
            user_id: User ID

        Returns:
            Number of settings
        """
        row = self._db.fetch_one(
            "SELECT COUNT(*) as count FROM user_settings WHERE user_id = ?", (user_id,)
        )
        return row.get("count", 0) if row else 0
