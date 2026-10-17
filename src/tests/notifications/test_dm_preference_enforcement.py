"""Regression tests: the per-user ``dm_notifications`` preference is enforced.

M2 (audit): the preference was stored and served by the settings CRUD but
never consulted by the notification fan-out, so a user who disabled DM
notifications still received notifications for mentions in direct messages.
The decision function now suppresses server-scope (``server_id is None``)
mentions when the preference is off.
"""

import pytest

from src.core.notifications.models import NotificationLevel, MentionType


@pytest.fixture
def dm_user(notification_manager, test_user):
    """A user with DM notifications explicitly disabled."""
    notification_manager.update_notification_settings(
        test_user.id, dm_notifications=False
    )
    return test_user


def _decide(manager, user, settings, server_id=None):
    return manager._should_notify_user_optimized(
        user_id=user.id,
        author_id=user.id + 1,
        server_id=server_id,
        channel_id=None,
        mention_type=MentionType.USER,
        settings=settings,
        override=None,
    )


class TestDmNotificationPreference:
    def test_dm_mention_suppressed_when_preference_disabled(
        self, notification_manager, dm_user
    ):
        settings = notification_manager.get_notification_settings(dm_user.id)
        assert settings.dm_notifications is False
        assert _decide(notification_manager, dm_user, settings, server_id=None) is False

    def test_dm_mention_allowed_when_preference_enabled(
        self, notification_manager, test_user
    ):
        settings = notification_manager.get_notification_settings(test_user.id)
        assert settings.dm_notifications is True
        assert (
            _decide(notification_manager, test_user, settings, server_id=None) is True
        )

    def test_server_mention_unaffected_by_dm_preference(
        self, notification_manager, dm_user
    ):
        """The preference must not leak into server-scope mentions."""
        settings = notification_manager.get_notification_settings(dm_user.id)
        assert settings.dm_notifications is False
        assert _decide(notification_manager, dm_user, settings, server_id=12345) is True

    def test_nothing_level_still_wins_for_dms(self, notification_manager, test_user):
        notification_manager.update_notification_settings(
            test_user.id, level=NotificationLevel.NOTHING, dm_notifications=True
        )
        settings = notification_manager.get_notification_settings(test_user.id)
        assert (
            _decide(notification_manager, test_user, settings, server_id=None) is False
        )
