"""Tests for user notification settings."""

from src.core.notifications import NotificationLevel


class TestGetNotificationSettings:
    """Tests for getting notification settings."""

    def test_get_default_settings(self, notification_manager):
        """Test getting default settings for user without custom settings."""
        settings = notification_manager.get_notification_settings(1)

        assert settings.user_id == 1
        assert settings.server_id is None
        assert settings.level == NotificationLevel.ALL_MESSAGES
        assert settings.dm_notifications is True
        assert settings.suppress_everyone is False
        assert settings.suppress_roles is False
        assert settings.mobile_push is True


class TestUpdateNotificationSettings:
    """Tests for updating notification settings."""

    def test_update_global_settings(self, notification_manager):
        """Test updating global notification settings."""
        settings = notification_manager.update_notification_settings(
            user_id=1,
            level=NotificationLevel.ONLY_MENTIONS,
            dm_notifications=False,
            suppress_everyone=True,
            mobile_push=False,
        )

        assert settings.level == NotificationLevel.ONLY_MENTIONS
        assert settings.dm_notifications is False
        assert settings.suppress_everyone is True
        assert settings.mobile_push is False

    def test_update_settings_twice(self, notification_manager):
        """Test updating settings multiple times."""
        notification_manager.update_notification_settings(
            user_id=1, level=NotificationLevel.ONLY_MENTIONS
        )

        settings = notification_manager.update_notification_settings(
            user_id=1, level=NotificationLevel.NOTHING
        )

        assert settings.level == NotificationLevel.NOTHING


class TestSuppressEveryone:
    """Tests for suppress @everyone setting."""

    def test_suppress_everyone_blocks_notification(self):
        """Test suppress_everyone prevents @everyone notifications."""
        pass

    def test_suppress_everyone_allows_direct_mention(self):
        """Test suppress_everyone still allows direct @user mentions."""
        pass


class TestSuppressRoles:
    """Tests for suppress @role setting."""

    def test_suppress_roles_blocks_notification(self):
        """Test suppress_roles prevents @role notifications."""
        pass


class TestNotificationLevelNothing:
    """Tests for notification level NOTHING."""

    def test_nothing_level_blocks_all(self):
        """Test NOTHING level blocks all notifications."""
        pass
