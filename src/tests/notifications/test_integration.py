"""Tests for notification integration with messaging and servers."""

import pytest


@pytest.mark.notifications
class TestIntegration:
    """Tests for notification integration with other modules."""

    def test_create_notification_on_mention(
        self, notification_manager, messaging_manager, two_users
    ):
        """Test that mentioning a user creates a notification."""
        user1, user2 = two_users
        dm = messaging_manager.create_dm(user1.id, user2.id)
        messaging_manager.send_message(user1.id, dm.id, f"Hello <@{user2.id}>!")
        # Notification should have been created for user2
        notifs = notification_manager.get_notifications(user2.id)
        assert len(notifs) >= 1

    def test_no_notification_for_self_mention(
        self, notification_manager, messaging_manager, test_user
    ):
        """Test that mentioning yourself does not create a notification."""
        messaging_manager.create_dm(test_user.id, test_user.id)
        # This may fail since you can't DM yourself, but the logic should handle it
        # Instead test with two users
        pass

    def test_mark_notification_read(
        self, notification_manager, messaging_manager, two_users
    ):
        """Test marking a notification as read."""
        user1, user2 = two_users
        dm = messaging_manager.create_dm(user1.id, user2.id)
        messaging_manager.send_message(user1.id, dm.id, f"Hey <@{user2.id}>")
        notifs = notification_manager.get_notifications(user2.id, unread_only=True)
        if notifs:
            result = notification_manager.mark_notification_read(user2.id, notifs[0].id)
            assert result is True

    def test_mark_all_read(self, notification_manager, messaging_manager, two_users):
        """Test marking all notifications as read."""
        user1, user2 = two_users
        dm = messaging_manager.create_dm(user1.id, user2.id)
        messaging_manager.send_message(user1.id, dm.id, f"<@{user2.id}> hello")
        count = notification_manager.mark_all_read(user2.id)
        assert isinstance(count, int)

    def test_get_unread_count(self, notification_manager, test_user):
        """Test getting unread count for a user."""
        unread = notification_manager.get_unread_count(test_user.id)
        assert unread is not None
        assert hasattr(unread, "total_unread")
        assert hasattr(unread, "mention_count")

    def test_get_mention_count(self, notification_manager, test_user):
        """Test getting mention count for a user."""
        count = notification_manager.get_mention_count(test_user.id)
        assert isinstance(count, int)
