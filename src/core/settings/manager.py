"""
Settings manager - handles CRUD operations for user settings.
"""

import re
from typing import Optional, Dict, List  # noqa: F401 (Dict/List re-exported via aliases)

import utils.logger as logger

from src.core.base import BaseManager, SnowflakeID
from .models import UserSetting, SettingsConfig
from .exceptions import (
    SettingsLimitExceeded,
    SettingsKeyTooLong,
    SettingsValueTooLong,
    SettingsKeyReserved,
    SettingsKeyInvalid,
)


class SettingsManager(BaseManager):
    """
    Manages user settings with configurable limits.

    Key contract: keys must match ``[a-z0-9_-]`` (case-insensitive), be
    non-empty after trimming, and stay within the configured length. The
    single-underscore namespace is reserved for server semantics and can only
    be written through dedicated server paths (:meth:`set_server_setting`).
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
        self._key_pattern = re.compile(f"[{self.config.key_charset}]+")

        logger.info(
            f"Settings manager initialized (max {self.config.max_settings_per_user} settings/user)"
        )

    def _validate_key(self, key: str) -> None:
        """Validate a setting key for user-facing writes.

        Enforces the charset contract, non-emptiness, length cap, and the
        reserved-prefix namespaces (double underscore internals AND the
        single-underscore server-semantics namespace).
        """
        if not isinstance(key, str) or not key.strip():
            raise SettingsKeyInvalid("Key must be a non-empty string")

        if len(key) > self.config.max_key_length:
            raise SettingsKeyTooLong(
                f"Key exceeds maximum length of {self.config.max_key_length} characters"
            )

        if not self._key_pattern.fullmatch(key):
            raise SettingsKeyInvalid(
                "Key may only contain letters, digits, hyphens and underscores"
            )

        for reserved in self.config.reserved_prefixes:
            if key.startswith(reserved):
                # Static message: never reflect the raw key — the bulk route
                # surfaces str(e) in per-key failure bodies.
                raise SettingsKeyReserved("Key uses a reserved prefix")

    def _validate_server_key(self, key: str) -> None:
        """Validate a key written through a dedicated server path.

        Server keys must satisfy charset/length but MAY live in the reserved
        single-underscore namespace, restricted to the known server key set.
        """
        if not isinstance(key, str) or not key.strip():
            raise SettingsKeyInvalid("Key must be a non-empty string")

        if len(key) > self.config.max_key_length:
            raise SettingsKeyTooLong(
                f"Key exceeds maximum length of {self.config.max_key_length} characters"
            )

        if not self._key_pattern.fullmatch(key):
            raise SettingsKeyInvalid(
                "Key may only contain letters, digits, hyphens and underscores"
            )

        for reserved in self.config.reserved_keys:
            if key.startswith(reserved):
                # Static message: never reflect the raw key (see _validate_key).
                raise SettingsKeyReserved("Key uses a reserved prefix")

        if key.startswith("_") and key not in self.config.server_setting_keys:
            raise SettingsKeyReserved("Key is not a recognised server-managed setting")

    def _is_readable_key(self, key: str) -> bool:
        """Whether a stored (possibly legacy) key may be surfaced on reads.

        Legacy rows predating the charset contract are rejected gracefully on
        read instead of poisoning API responses; recognised server-managed
        keys remain readable despite their underscore prefix.
        """
        if isinstance(key, str) and self._key_pattern.fullmatch(key):
            return True
        return isinstance(key, str) and key in self.config.server_setting_keys

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
            if not self._is_readable_key(key):
                # Legacy junk key predating the charset contract: reject
                # gracefully on read instead of surfacing it.
                logger.debug(f"Ignoring legacy key '{key}' for user {user_id}")
                return None
            logger.debug(f"Retrieved setting '{key}' for user {user_id}")
            return row.get("value")
        return None

    def get_one(self, user_id: SnowflakeID, key: str) -> Optional[UserSetting]:
        """
        Get a single setting as a ``UserSetting`` object using a targeted query.

        Args:
            user_id: User ID
            key: Setting key

        Returns:
            The ``UserSetting`` or None if not found
        """
        row = self._db.fetch_one(
            "SELECT id, user_id, key, value, created_at, updated_at "
            "FROM user_settings WHERE user_id = ? AND key = ?",
            (user_id, key),
        )

        if row:
            if not self._is_readable_key(key):
                # Legacy junk key predating the charset contract: reject
                # gracefully on read instead of surfacing it.
                logger.debug(f"Ignoring legacy key '{key}' for user {user_id}")
                return None
            logger.debug(f"Retrieved setting '{key}' for user {user_id}")
            return UserSetting(
                id=row.get("id"),
                user_id=row.get("user_id"),
                key=row.get("key"),
                value=row.get("value"),
                created_at=row.get("created_at"),
                updated_at=row.get("updated_at"),
            )
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

        # Inserting a new key: enforce the per-user limit atomically so that
        # concurrent requests cannot both pass the check and exceed the limit.
        self._db.begin_transaction()
        try:
            count = self.get_count(user_id)
            if count >= self.config.max_settings_per_user:
                raise SettingsLimitExceeded(
                    f"User has reached maximum of {self.config.max_settings_per_user} settings"
                )

            setting_id = self._generate_id()

            self._db.execute(
                """INSERT INTO user_settings (id, user_id, key, value, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (setting_id, user_id, key, value, now, now),
            )
        except Exception:
            self._db.rollback()
            raise
        else:
            self._db.commit()

        logger.info(f"Created setting '{key}' for user {user_id}")
        return UserSetting(
            id=setting_id,
            user_id=user_id,
            key=key,
            value=value,
            created_at=now,
            updated_at=now,
        )

    def set_server_setting(
        self, user_id: SnowflakeID, key: str, value: str
    ) -> UserSetting:
        """Persist a server-semantics setting (reserved ``_`` namespace).

        Only callable for keys in :attr:`SettingsConfig.server_setting_keys`;
        the generic user-facing write paths route through :meth:`set` which
        rejects the whole reserved namespace.
        """
        self._validate_server_key(key)
        self._validate_value(value)

        now = self._get_timestamp()

        existing = self._db.fetch_one(
            "SELECT id, created_at FROM user_settings WHERE user_id = ? AND key = ?",
            (user_id, key),
        )

        if existing:
            setting_id = existing.get("id")
            created_at = existing.get("created_at")
            self._db.execute(
                "UPDATE user_settings SET value = ?, updated_at = ? WHERE id = ?",
                (value, now, setting_id),
            )
            logger.info(f"Updated server setting '{key}' for user {user_id}")
            return UserSetting(
                id=setting_id,
                user_id=user_id,
                key=key,
                value=value,
                created_at=created_at,
                updated_at=now,
            )

        setting_id = self._generate_id()
        self._db.execute(
            """INSERT INTO user_settings (id, user_id, key, value, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (setting_id, user_id, key, value, now, now),
        )
        logger.info(f"Created server setting '{key}' for user {user_id}")
        return UserSetting(
            id=setting_id,
            user_id=user_id,
            key=key,
            value=value,
            created_at=now,
            updated_at=now,
        )

    def set_many(
        self, user_id: SnowflakeID, items: Dict[str, str]
    ) -> Dict[str, UserSetting]:
        """Atomically write multiple settings in a single transaction.

        Every key/value is validated up-front; then all inserts/updates and
        the per-user limit check run inside one transaction so a mid-batch
        failure rolls back the entire batch (no partial state).

        Raises:
            SettingsBulkLimitExceeded: If the batch would exceed the per-user
                settings limit.
        """
        for key, value in items.items():
            self._validate_key(key)
            self._validate_value(value)

        now = self._get_timestamp()
        results: Dict[str, UserSetting] = {}

        self._db.begin_transaction()
        try:
            count = self.get_count(user_id)
            new_keys = [k for k in items if not self._exists(user_id, k)]
            if count + len(new_keys) > self.config.max_settings_per_user:
                raise SettingsLimitExceeded(
                    f"Bulk write would exceed maximum of "
                    f"{self.config.max_settings_per_user} settings"
                )

            for key, value in items.items():
                existing = self._db.fetch_one(
                    "SELECT id, created_at FROM user_settings WHERE user_id = ? AND key = ?",
                    (user_id, key),
                )
                if existing:
                    setting_id = existing.get("id")
                    created_at = existing.get("created_at")
                    self._db.execute(
                        "UPDATE user_settings SET value = ?, updated_at = ? WHERE id = ?",
                        (value, now, setting_id),
                    )
                    results[key] = UserSetting(
                        id=setting_id,
                        user_id=user_id,
                        key=key,
                        value=value,
                        created_at=created_at,
                        updated_at=now,
                    )
                else:
                    setting_id = self._generate_id()
                    self._db.execute(
                        """INSERT INTO user_settings (id, user_id, key, value, created_at, updated_at)
                           VALUES (?, ?, ?, ?, ?, ?)""",
                        (setting_id, user_id, key, value, now, now),
                    )
                    results[key] = UserSetting(
                        id=setting_id,
                        user_id=user_id,
                        key=key,
                        value=value,
                        created_at=now,
                        updated_at=now,
                    )
        except Exception:
            self._db.rollback()
            raise
        else:
            self._db.commit()

        logger.info(f"Bulk set {len(results)} settings for user {user_id}")
        return results

    def _exists(self, user_id: SnowflakeID, key: str) -> bool:
        """Return True when a setting row already exists (inside callers' txn)."""
        row = self._db.fetch_one(
            "SELECT 1 FROM user_settings WHERE user_id = ? AND key = ?",
            (user_id, key),
        )
        return row is not None

    def delete(self, user_id: SnowflakeID, key: str) -> bool:
        """
        Delete a setting.

        Args:
            user_id: User ID
            key: Setting key

        Returns:
            True if deleted, False if not found

        Raises:
            SettingsKeyInvalid: If key fails the charset/format contract
            SettingsKeyTooLong: If key is too long
            SettingsKeyReserved: If key uses a reserved prefix
        """
        # Validate before touching the DB: evil keys must not reach the
        # DELETE statement nor the cache-invalidation patterns built from
        # the key downstream (cache pattern injection).
        self._validate_key(key)
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

        result = {}
        dropped = 0
        for row in rows:
            key = row.get("key")
            if not self._is_readable_key(key):
                dropped += 1
                continue
            result[key] = row.get("value")
        if dropped:
            logger.debug(f"Filtered {dropped} legacy keys for user {user_id} on read")
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

        settings_list = []
        for row in rows:
            if not self._is_readable_key(row.get("key")):
                continue
            settings_list.append(
                UserSetting(
                    id=row.get("id"),
                    user_id=row.get("user_id"),
                    key=row.get("key"),
                    value=row.get("value"),
                    created_at=row.get("created_at"),
                    updated_at=row.get("updated_at"),
                )
            )
        return settings_list

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

    # ------------------------------------------------------------------ #
    # Long-name / test-friendly aliases                                  #
    # ------------------------------------------------------------------ #
    # The internal API uses short names (`get`, `set`, ...) per Python
    # convention; the public alias set mirrors what the integration tests
    # (and a growing chunk of the route layer) call explicitly.

    def set_setting(self, user_id: SnowflakeID, key: str, value: str) -> "UserSetting":
        return self.set(user_id, key, value)

    def get_setting(self, user_id: SnowflakeID, key: str) -> Optional[str]:
        return self.get(user_id, key)

    def delete_setting(self, user_id: SnowflakeID, key: str) -> bool:
        return self.delete(user_id, key)

    def get_all_settings(self, user_id: SnowflakeID) -> Dict[str, str]:
        return self.get_all(user_id)

    def get_settings_count(self, user_id: SnowflakeID) -> int:
        return self.get_count(user_id)

    def get_settings_list(self, user_id: SnowflakeID) -> List[UserSetting]:
        return self.get_all_as_list(user_id)
