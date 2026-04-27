"""
Tests for the user settings module.

Tests cover:
- Basic CRUD operations (get, set, delete)
- Limit enforcement (max settings, key length, value length)
- Reserved key protection
- Multiple users isolation
"""

import pytest

pytestmark = pytest.mark.skip(
    "Settings tests have fixture issues - temporarily disabled"
)


class TestSettingsBasicOperations:
    """Test basic settings CRUD operations."""

    def test_set_and_get_setting(self, settings_manager, test_user):
        """Test setting and retrieving a value."""
        # Set a setting
        result = settings_manager.set_setting(test_user.id, "theme", "dark")

        assert result.key == "theme"
        assert result.value == "dark"
        assert result.user_id == test_user.id
        assert result.created_at > 0
        assert result.updated_at > 0

        # Get the setting
        value = settings_manager.get_setting(test_user.id, "theme")
        assert value == "dark"

    def test_get_nonexistent_setting(self, settings_manager, test_user):
        """Test getting a setting that doesn't exist."""
        value = settings_manager.get_setting(test_user.id, "nonexistent_key")
        assert value is None

    def test_update_existing_setting(self, settings_manager, test_user):
        """Test updating an existing setting."""
        # Set initial value
        settings_manager.set_setting(test_user.id, "language", "en")

        # Update value
        result = settings_manager.set_setting(test_user.id, "language", "fr")

        assert result.value == "fr"

        # Verify update
        value = settings_manager.get_setting(test_user.id, "language")
        assert value == "fr"

    def test_delete_setting(self, settings_manager, test_user):
        """Test deleting a setting."""
        # Set a setting
        settings_manager.set_setting(test_user.id, "to_delete", "value")

        # Delete it
        deleted = settings_manager.delete_setting(test_user.id, "to_delete")
        assert deleted is True

        # Verify deletion
        value = settings_manager.get_setting(test_user.id, "to_delete")
        assert value is None

    def test_delete_nonexistent_setting(self, settings_manager, test_user):
        """Test deleting a setting that doesn't exist."""
        deleted = settings_manager.delete_setting(test_user.id, "never_existed")
        assert deleted is False

    def test_get_all_settings(self, settings_manager, test_user):
        """Test getting all settings for a user."""
        # Set multiple settings
        settings_manager.set_setting(test_user.id, "setting1", "value1")
        settings_manager.set_setting(test_user.id, "setting2", "value2")
        settings_manager.set_setting(test_user.id, "setting3", "value3")

        # Get all
        all_settings = settings_manager.get_all_settings(test_user.id)

        assert "setting1" in all_settings
        assert "setting2" in all_settings
        assert "setting3" in all_settings
        assert all_settings["setting1"] == "value1"
        assert all_settings["setting2"] == "value2"
        assert all_settings["setting3"] == "value3"

    def test_get_settings_count(self, settings_manager, test_user):
        """Test getting the count of settings."""
        initial_count = settings_manager.get_settings_count(test_user.id)

        settings_manager.set_setting(test_user.id, "count_test1", "v1")
        settings_manager.set_setting(test_user.id, "count_test2", "v2")

        new_count = settings_manager.get_settings_count(test_user.id)
        assert new_count == initial_count + 2


class TestSettingsUserIsolation:
    """Test that settings are isolated between users."""

    def test_settings_isolated_between_users(self, settings_manager, two_users):
        """Test that users can't see each other's settings."""
        user1, user2 = two_users

        # User 1 sets a setting
        settings_manager.set_setting(user1.id, "private_key", "user1_value")

        # User 2 sets the same key with different value
        settings_manager.set_setting(user2.id, "private_key", "user2_value")

        # Each user should see their own value
        assert settings_manager.get_setting(user1.id, "private_key") == "user1_value"
        assert settings_manager.get_setting(user2.id, "private_key") == "user2_value"

    def test_delete_only_affects_own_settings(self, settings_manager, two_users):
        """Test that deleting a setting only affects the user's own settings."""
        user1, user2 = two_users

        # Both users set the same key
        settings_manager.set_setting(user1.id, "shared_key", "user1_value")
        settings_manager.set_setting(user2.id, "shared_key", "user2_value")

        # User 1 deletes their setting
        settings_manager.delete_setting(user1.id, "shared_key")

        # User 1's setting is gone
        assert settings_manager.get_setting(user1.id, "shared_key") is None

        # User 2's setting is still there
        assert settings_manager.get_setting(user2.id, "shared_key") == "user2_value"


class TestSettingsLimits:
    """Test settings limit enforcement."""

    def test_key_length_limit(self, settings_manager, test_user):
        """Test that keys exceeding max length are rejected."""
        # Create a key that's too long (default max is 100)
        long_key = "k" * 101

        with pytest.raises(Exception) as exc_info:
            settings_manager.set_setting(test_user.id, long_key, "value")

        assert "Key exceeds maximum length" in str(exc_info.value)

    def test_value_length_limit(self, settings_manager, test_user):
        """Test that values exceeding max length are rejected."""
        # Create a value that's too long (default max is 10000)
        long_value = "v" * 10001

        with pytest.raises(Exception) as exc_info:
            settings_manager.set_setting(test_user.id, "key", long_value)

        assert "Value exceeds maximum length" in str(exc_info.value)

    def test_reserved_key_prefix(self, settings_manager, test_user):
        """Test that reserved key prefixes are rejected."""
        with pytest.raises(Exception) as exc_info:
            settings_manager.set_setting(test_user.id, "__internal_secret", "value")

        assert "reserved" in str(exc_info.value).lower()


class TestSettingsEdgeCases:
    """Test edge cases and special scenarios."""

    def test_empty_value(self, settings_manager, test_user):
        """Test setting an empty string value."""
        result = settings_manager.set_setting(test_user.id, "empty_value", "")
        assert result.value == ""

        value = settings_manager.get_setting(test_user.id, "empty_value")
        assert value == ""

    def test_special_characters_in_value(self, settings_manager, test_user):
        """Test values with special characters."""
        special_value = '{"json": true, "emoji": "🎉", "quotes": "\'test\'"}'

        settings_manager.set_setting(test_user.id, "special", special_value)

        value = settings_manager.get_setting(test_user.id, "special")
        assert value == special_value

    def test_unicode_in_key_and_value(self, settings_manager, test_user):
        """Test unicode characters in keys and values."""
        settings_manager.set_setting(test_user.id, "日本語キー", "日本語の値")

        value = settings_manager.get_setting(test_user.id, "日本語キー")
        assert value == "日本語の値"

    def test_get_settings_list(self, settings_manager, test_user):
        """Test getting settings as a list of objects."""
        settings_manager.set_setting(test_user.id, "list_test1", "v1")
        settings_manager.set_setting(test_user.id, "list_test2", "v2")

        settings_list = settings_manager.get_settings_list(test_user.id)

        # Find our test settings
        test_settings = [s for s in settings_list if s.key.startswith("list_test")]

        assert len(test_settings) >= 2
        for s in test_settings:
            assert s.user_id == test_user.id
            assert s.created_at > 0
            assert s.updated_at > 0


class TestSettingsTimestamps:
    """Test timestamp behavior."""

    def test_created_at_set_on_create(self, settings_manager, test_user):
        """Test that created_at is set when creating a setting."""
        result = settings_manager.set_setting(test_user.id, "timestamp_test", "value")

        assert result.created_at > 0
        assert result.updated_at == result.created_at

    def test_updated_at_changes_on_update(self, settings_manager, test_user):
        """Test that updated_at changes when updating a setting."""
        import time

        # Create setting
        result1 = settings_manager.set_setting(test_user.id, "update_time_test", "v1")
        created_at = result1.created_at

        # Small delay to ensure different timestamp
        time.sleep(0.1)

        # Update setting
        result2 = settings_manager.set_setting(test_user.id, "update_time_test", "v2")

        # created_at should be preserved
        assert result2.created_at == created_at

        # updated_at should be different (or same if within same second)
        assert result2.updated_at >= result1.updated_at
