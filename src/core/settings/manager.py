"""
Settings manager - handles CRUD operations for user settings.
"""

import time
import sys
import os
from typing import Optional, Dict, List

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../"))
common_utils_path = os.path.join(project_root, "src", "utils", "common-utils")
if common_utils_path not in sys.path:
    sys.path.append(common_utils_path)

import utils.logger as logger

from .models import UserSetting, SettingsConfig
from .schema import create_tables
from .exceptions import (
    SettingsLimitExceeded,
    SettingsKeyTooLong,
    SettingsValueTooLong,
    SettingsKeyReserved,
    SettingsNotFound,
)


class SettingsManager:
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
        self.db = db
        self.config = config or SettingsConfig()
        
        # Create tables on init
        create_tables(db)
        logger.info(f"Settings manager initialized (max {self.config.max_settings_per_user} settings/user)")
    
    def _validate_key(self, key: str) -> None:
        """Validate a setting key."""
        if len(key) > self.config.max_key_length:
            raise SettingsKeyTooLong(
                f"Key exceeds maximum length of {self.config.max_key_length} characters"
            )
        
        for reserved in self.config.reserved_keys:
            if key.startswith(reserved):
                raise SettingsKeyReserved(f"Key '{key}' uses reserved prefix '{reserved}'")
    
    def _validate_value(self, value: str) -> None:
        """Validate a setting value."""
        if len(value) > self.config.max_value_length:
            raise SettingsValueTooLong(
                f"Value exceeds maximum length of {self.config.max_value_length} characters"
            )
    
    def get(self, user_id: int, key: str) -> Optional[str]:
        """
        Get a single setting value.
        
        Args:
            user_id: User ID
            key: Setting key
            
        Returns:
            Setting value or None if not found
        """
        row = self.db.fetch_one(
            "SELECT value FROM user_settings WHERE user_id = ? AND key = ?",
            (user_id, key)
        )
        
        if row:
            logger.debug(f"Retrieved setting '{key}' for user {user_id}")
            return row[0]
        return None
    
    def set(self, user_id: int, key: str, value: str) -> UserSetting:
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
        
        now = int(time.time())
        
        # Check if setting exists
        existing = self.db.fetch_one(
            "SELECT id, created_at FROM user_settings WHERE user_id = ? AND key = ?",
            (user_id, key)
        )
        
        if existing:
            # Update existing
            self.db.execute(
                "UPDATE user_settings SET value = ?, updated_at = ? WHERE id = ?",
                (value, now, existing[0])
            )
            logger.info(f"Updated setting '{key}' for user {user_id}")
            return UserSetting(
                id=existing[0],
                user_id=user_id,
                key=key,
                value=value,
                created_at=existing[1],
                updated_at=now
            )
        
        # Check limit before inserting
        count = self.get_count(user_id)
        if count >= self.config.max_settings_per_user:
            raise SettingsLimitExceeded(
                f"User has reached maximum of {self.config.max_settings_per_user} settings"
            )
        
        # Insert new
        cursor = self.db.execute(
            """INSERT INTO user_settings (user_id, key, value, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?)""",
            (user_id, key, value, now, now)
        )
        setting_id = cursor.lastrowid
        cursor.close()
        
        logger.info(f"Created setting '{key}' for user {user_id}")
        return UserSetting(
            id=setting_id,
            user_id=user_id,
            key=key,
            value=value,
            created_at=now,
            updated_at=now
        )
    
    def delete(self, user_id: int, key: str) -> bool:
        """
        Delete a setting.
        
        Args:
            user_id: User ID
            key: Setting key
            
        Returns:
            True if deleted, False if not found
        """
        cursor = self.db.execute(
            "DELETE FROM user_settings WHERE user_id = ? AND key = ?",
            (user_id, key)
        )
        deleted = cursor.rowcount > 0
        cursor.close()
        
        if deleted:
            logger.info(f"Deleted setting '{key}' for user {user_id}")
        return deleted
    
    def get_all(self, user_id: int) -> Dict[str, str]:
        """
        Get all settings for a user as a dictionary.
        
        Args:
            user_id: User ID
            
        Returns:
            Dictionary of key-value pairs
        """
        rows = self.db.fetch_all(
            "SELECT key, value FROM user_settings WHERE user_id = ?",
            (user_id,)
        )
        
        result = {row[0]: row[1] for row in rows}
        logger.debug(f"Retrieved {len(result)} settings for user {user_id}")
        return result
    
    def get_all_as_list(self, user_id: int) -> List[UserSetting]:
        """
        Get all settings for a user as a list of UserSetting objects.
        
        Args:
            user_id: User ID
            
        Returns:
            List of UserSetting objects
        """
        rows = self.db.fetch_all(
            "SELECT id, user_id, key, value, created_at, updated_at FROM user_settings WHERE user_id = ?",
            (user_id,)
        )
        
        return [
            UserSetting(
                id=row[0],
                user_id=row[1],
                key=row[2],
                value=row[3],
                created_at=row[4],
                updated_at=row[5]
            )
            for row in rows
        ]
    
    def get_count(self, user_id: int) -> int:
        """
        Get the count of settings for a user.
        
        Args:
            user_id: User ID
            
        Returns:
            Number of settings
        """
        row = self.db.fetch_one(
            "SELECT COUNT(*) FROM user_settings WHERE user_id = ?",
            (user_id,)
        )
        return row[0] if row else 0
