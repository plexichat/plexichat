"""
Tests for bug fixes in the user settings system.

Covers:
- SET-2: Per-user limit is enforced atomically (no TOCTOU bypass)
- SET-7: GET /{key} uses a targeted query (get_one)
- SET-9: Dead value_encrypted column removed from the schema
"""

import pytest

from src.core.settings.manager import SettingsManager, SettingsConfig
from src.core.settings.exceptions import SettingsLimitExceeded
from src.core.settings import schema as settings_schema


class TestSet2LimitEnforcement:
    """SET-2: the per-user limit must be enforced exactly, no off-by-one."""

    def test_setting_beyond_limit_raises(self, db, test_user):
        """Inserting one more than the limit raises SettingsLimitExceeded."""
        manager = SettingsManager(db, config=SettingsConfig(max_settings_per_user=5))

        for i in range(5):
            manager.set_setting(test_user.id, f"key_{i}", f"value_{i}")

        with pytest.raises(SettingsLimitExceeded):
            manager.set_setting(test_user.id, "key_overflow", "overflow")

    def test_limit_exactly_reachable(self, db, test_user):
        """Exactly `limit` settings can be stored without error."""
        manager = SettingsManager(db, config=SettingsConfig(max_settings_per_user=5))

        for i in range(5):
            result = manager.set_setting(test_user.id, f"key_{i}", f"value_{i}")
            assert result.key == f"key_{i}"

        assert manager.get_settings_count(test_user.id) == 5


class TestSet7TargetedGet:
    """SET-7: a single setting is fetched with a targeted query, not a full scan."""

    def test_get_one_returns_setting(self, settings_manager, test_user):
        settings_manager.set_setting(test_user.id, "theme", "dark")

        setting = settings_manager.get_one(test_user.id, "theme")
        assert setting is not None
        assert setting.key == "theme"
        assert setting.value == "dark"

    def test_get_one_missing_returns_none(self, settings_manager, test_user):
        assert settings_manager.get_one(test_user.id, "does_not_exist") is None

    def test_get_one_isolated_between_users(self, settings_manager, two_users):
        user1, user2 = two_users
        settings_manager.set_setting(user1.id, "theme", "dark")
        settings_manager.set_setting(user2.id, "theme", "light")

        assert settings_manager.get_one(user1.id, "theme").value == "dark"
        assert settings_manager.get_one(user2.id, "theme").value == "light"


class TestSet9DeadColumn:
    """SET-9: the unused value_encrypted column must be gone from the schema."""

    def test_schema_has_no_value_encrypted(self):
        assert "value_encrypted" not in settings_schema.SCHEMA_SQLITE
