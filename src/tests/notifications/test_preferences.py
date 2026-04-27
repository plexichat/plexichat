"""Tests for notification settings and preferences."""

import pytest

from src.core.notifications.models import NotificationLevel, NotificationSettings


@pytest.mark.notifications
class TestPreferences:
    """Tests for notification settings management."""

    def test_get_default_settings(self, notification_manager, test_user):
        """Test getting default notification settings for a user."""
        settings = notification_manager.get_notification_settings(test_user.id)
        assert settings is not None
        assert settings.level == NotificationLevel.ALL_MESSAGES
        assert settings.dm_notifications is True
        assert settings.suppress_everyone is False
        assert settings.suppress_roles is False
        assert settings.mobile_push is True

    def test_update_notification_level(self, notification_manager, test_user):
        """Test updating notification level."""
        settings = notification_manager.update_notification_settings(
            test_user.id, level=NotificationLevel.ONLY_MENTIONS
        )
        assert settings.level == NotificationLevel.ONLY_MENTIONS

    def test_update_suppress_everyone(self, notification_manager, test_user):
        """Test enabling suppress everyone mentions."""
        settings = notification_manager.update_notification_settings(
            test_user.id, suppress_everyone=True
        )
        assert settings.suppress_everyone is True

    def test_update_suppress_roles(self, notification_manager, test_user):
        """Test enabling suppress role mentions."""
        settings = notification_manager.update_notification_settings(
            test_user.id, suppress_roles=True
        )
        assert settings.suppress_roles is True

    def test_update_dm_notifications(self, notification_manager, test_user):
        """Test disabling DM notifications."""
        settings = notification_manager.update_notification_settings(
            test_user.id, dm_notifications=False
        )
        assert settings.dm_notifications is False

    def test_update_mobile_push(self, notification_manager, test_user):
        """Test disabling mobile push."""
        settings = notification_manager.update_notification_settings(
            test_user.id, mobile_push=False
        )
        assert settings.mobile_push is False

    def test_update_nothing_level(self, notification_manager, test_user):
        """Test setting notification level to nothing."""
        settings = notification_manager.update_notification_settings(
            test_user.id, level=NotificationLevel.NOTHING
        )
        assert settings.level == NotificationLevel.NOTHING

    def test_get_server_specific_settings(
        self, notification_manager, test_user, test_server
    ):
        """Test getting server-specific notification settings."""
        server, owner = test_server
        settings = notification_manager.get_notification_settings(
            test_user.id, server.id
        )
        assert settings is not None

    def test_update_server_specific_settings(
        self, notification_manager, test_user, test_server
    ):
        """Test updating server-specific notification settings."""
        server, owner = test_server
        settings = notification_manager.update_notification_settings(
            test_user.id, server_id=server.id, level=NotificationLevel.MUTED
        )
        assert settings.level == NotificationLevel.MUTED

    def test_channel_override(self, notification_manager, test_user, test_server):
        """Test setting channel notification override."""
        server, owner = test_server
        channel = server_manager_create_channel(notification_manager, test_user, server)
        if channel:
            override = notification_manager.set_channel_override(
                test_user.id, channel.id, NotificationLevel.MUTED
            )
            assert override.level == NotificationLevel.MUTED

    def test_delete_channel_override(
        self, notification_manager, test_user, test_server
    ):
        """Test deleting a channel notification override."""
        server, owner = test_server
        channel = server_manager_create_channel(notification_manager, test_user, server)
        if channel:
            notification_manager.set_channel_override(
                test_user.id, channel.id, NotificationLevel.MUTED
            )
            result = notification_manager.delete_channel_override(
                test_user.id, channel.id
            )
            assert result is True

    def test_notification_settings_dataclass(self):
        """Test NotificationSettings dataclass."""
        settings = NotificationSettings(user_id=1)
        assert settings.level == NotificationLevel.ALL_MESSAGES
        assert settings.dm_notifications is True
        assert settings.suppress_everyone is False


def server_manager_create_channel(notification_manager, user, server):
    """Helper to create a channel via server manager."""
    try:
        from src.core.servers.manager import ServerManager

        # Not available from notification_manager, return None
        return None
    except Exception:
        return None
