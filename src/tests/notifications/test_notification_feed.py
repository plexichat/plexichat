"""
Tests for notification feed functionality.
"""

import pytest
from src.core.notifications import MentionType


class TestGetNotificationFeed:
    """Tests for getting notification feed."""

    def test_empty_feed(self, fresh_users):
        """Test empty feed for new user."""
        user1, user2, auth, messaging, servers, relationships, presence, notifications = fresh_users

        feed = notifications.get_notification_feed(user1.id)

        assert len(feed.notifications) == 0
        assert feed.total_count == 0
        assert feed.unread_count == 0
        assert feed.has_more is False

    def test_feed_with_notifications(self, users_with_dm):
        """Test feed with notifications."""
        user1, user2, dm, messaging, notifications = users_with_dm

        content = f"<@{user2.id}> check this"
        msg = messaging.send_message(user1.id, dm.id, content)

        notifications.create_notifications_for_message(
            sender_id=user1.id,
            message_id=msg.id,
            conversation_id=dm.id,
            content=content
        )

        feed = notifications.get_notification_feed(user2.id)

        assert len(feed.notifications) >= 1
        assert feed.total_count >= 1
        assert feed.unread_count >= 1

    def test_feed_order(self, group_conversation):
        """Test feed is ordered by most recent first."""
        owner, member1, member2, group, messaging, notifications, relationships = group_conversation

        for i in range(3):
            content = f"<@{member1.id}> message {i}"
            msg = messaging.send_message(owner.id, group.id, content)
            notifications.create_notifications_for_message(
                sender_id=owner.id,
                message_id=msg.id,
                conversation_id=group.id,
                content=content
            )

        feed = notifications.get_notification_feed(member1.id)

        assert len(feed.notifications) >= 3
        for i in range(len(feed.notifications) - 1):
            assert feed.notifications[i].created_at >= feed.notifications[i + 1].created_at

    def test_feed_pagination(self, group_conversation):
        """Test feed pagination with before_id."""
        owner, member1, member2, group, messaging, notifications, relationships = group_conversation

        for i in range(5):
            content = f"<@{member1.id}> message {i}"
            msg = messaging.send_message(owner.id, group.id, content)
            notifications.create_notifications_for_message(
                sender_id=owner.id,
                message_id=msg.id,
                conversation_id=group.id,
                content=content
            )

        feed1 = notifications.get_notification_feed(member1.id, limit=2)

        assert len(feed1.notifications) == 2

        if len(feed1.notifications) > 0:
            last_id = feed1.notifications[-1].id
            feed2 = notifications.get_notification_feed(member1.id, limit=2, before_id=last_id)

            for notif in feed2.notifications:
                assert notif.id < last_id

    def test_feed_has_more(self, group_conversation):
        """Test feed has_more flag."""
        owner, member1, member2, group, messaging, notifications, relationships = group_conversation

        for i in range(5):
            content = f"<@{member1.id}> message {i}"
            msg = messaging.send_message(owner.id, group.id, content)
            notifications.create_notifications_for_message(
                sender_id=owner.id,
                message_id=msg.id,
                conversation_id=group.id,
                content=content
            )

        feed = notifications.get_notification_feed(member1.id, limit=2)

        assert feed.has_more is True


class TestGetNotifications:
    """Tests for getting notifications list."""

    def test_get_notifications(self, users_with_dm):
        """Test getting notifications list."""
        user1, user2, dm, messaging, notifications = users_with_dm

        content = f"<@{user2.id}>"
        msg = messaging.send_message(user1.id, dm.id, content)

        notifications.create_notifications_for_message(
            sender_id=user1.id,
            message_id=msg.id,
            conversation_id=dm.id,
            content=content
        )

        notifs = notifications.get_notifications(user2.id)

        assert len(notifs) >= 1

    def test_get_unread_only(self, group_conversation):
        """Test getting only unread notifications."""
        owner, member1, member2, group, messaging, notifications, relationships = group_conversation

        for i in range(3):
            content = f"<@{member1.id}> message {i}"
            msg = messaging.send_message(owner.id, group.id, content)
            notifications.create_notifications_for_message(
                sender_id=owner.id,
                message_id=msg.id,
                conversation_id=group.id,
                content=content
            )

        all_notifs = notifications.get_notifications(member1.id)
        if len(all_notifs) > 0:
            notifications.mark_notification_read(member1.id, all_notifs[0].id)

        unread_notifs = notifications.get_notifications(member1.id, unread_only=True)

        assert len(unread_notifs) < len(all_notifs)

    def test_get_notifications_pagination(self, group_conversation):
        """Test notifications pagination."""
        owner, member1, member2, group, messaging, notifications, relationships = group_conversation

        for i in range(5):
            content = f"<@{member1.id}> message {i}"
            msg = messaging.send_message(owner.id, group.id, content)
            notifications.create_notifications_for_message(
                sender_id=owner.id,
                message_id=msg.id,
                conversation_id=group.id,
                content=content
            )

        page1 = notifications.get_notifications(member1.id, limit=2)

        assert len(page1) == 2

        if len(page1) > 0:
            page2 = notifications.get_notifications(member1.id, limit=2, before_id=page1[-1].id)

            for notif in page2:
                assert notif.id < page1[-1].id


class TestDeleteNotification:
    """Tests for deleting notifications."""

    def test_delete_notification(self, users_with_dm):
        """Test deleting a notification."""
        user1, user2, dm, messaging, notifications = users_with_dm

        content = f"<@{user2.id}>"
        msg = messaging.send_message(user1.id, dm.id, content)

        notifs = notifications.create_notifications_for_message(
            sender_id=user1.id,
            message_id=msg.id,
            conversation_id=dm.id,
            content=content
        )

        assert len(notifs) == 1

        result = notifications.delete_notification(user2.id, notifs[0].id)

        assert result is True

        notif = notifications.get_notification(notifs[0].id)
        assert notif is None

    def test_delete_nonexistent_notification(self, fresh_users):
        """Test deleting nonexistent notification raises error."""
        user1, user2, auth, messaging, servers, relationships, presence, notifications = fresh_users

        from src.core.notifications import NotificationNotFoundError

        with pytest.raises(NotificationNotFoundError):
            notifications.delete_notification(user1.id, 999999999)

    def test_delete_other_users_notification(self, users_with_dm):
        """Test cannot delete another user's notification."""
        user1, user2, dm, messaging, notifications = users_with_dm

        content = f"<@{user2.id}>"
        msg = messaging.send_message(user1.id, dm.id, content)

        notifs = notifications.create_notifications_for_message(
            sender_id=user1.id,
            message_id=msg.id,
            conversation_id=dm.id,
            content=content
        )

        from src.core.notifications import NotificationNotFoundError

        with pytest.raises(NotificationNotFoundError):
            notifications.delete_notification(user1.id, notifs[0].id)
