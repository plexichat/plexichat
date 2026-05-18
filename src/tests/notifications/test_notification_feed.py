"""Tests for notification feed functionality."""

import pytest
from unittest.mock import patch
from src.utils import encryption


class TestGetNotificationFeed:
    """Tests for getting notification feed."""

    def test_empty_feed(self, notification_manager):
        """Test empty feed for new user."""
        feed = notification_manager.get_notification_feed(1)

        assert len(feed.notifications) == 0
        assert feed.total_count == 0
        assert feed.unread_count == 0
        assert feed.has_more is False

    def test_feed_with_notifications(
        self, auth_manager, messaging_manager, notification_manager
    ):
        """Test feed with notifications."""
        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user1 = auth_manager.register(
                username="user1", email="user1@example.com", password="TestPass123!"
            )
            user2 = auth_manager.register(
                username="user2", email="user2@example.com", password="TestPass123!"
            )

        dm = messaging_manager.create_dm(user1.id, user2.id)

        content = f"<@{user2.id}> check this"
        msg = messaging_manager.send_message(user1.id, dm.id, content)

        notification_manager.create_notifications_for_message(
            author_id=user1.id,
            message_id=msg.id,
            conversation_id=dm.id,
            content=content,
        )

        feed = notification_manager.get_notification_feed(user2.id)

        assert len(feed.notifications) >= 1
        assert feed.total_count >= 1
        assert feed.unread_count >= 1

    def test_feed_order(self, auth_manager, messaging_manager, notification_manager):
        """Test feed is ordered by most recent first."""
        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            owner = auth_manager.register(
                username="groupowner",
                email="owner@example.com",
                password="TestPass123!",
            )
            member1 = auth_manager.register(
                username="member1", email="member1@example.com", password="TestPass123!"
            )
            member2 = auth_manager.register(
                username="member2", email="member2@example.com", password="TestPass123!"
            )

        group = messaging_manager.create_group(
            owner.id, "Test Group", [member1.id, member2.id]
        )

        for i in range(3):
            content = f"<@{member1.id}> message {i}"
            msg = messaging_manager.send_message(owner.id, group.id, content)
            notification_manager.create_notifications_for_message(
                author_id=owner.id,
                message_id=msg.id,
                conversation_id=group.id,
                content=content,
            )

        feed = notification_manager.get_notification_feed(member1.id)

        assert len(feed.notifications) >= 3
        for i in range(len(feed.notifications) - 1):
            assert (
                feed.notifications[i].created_at >= feed.notifications[i + 1].created_at
            )

    def test_feed_pagination(
        self, auth_manager, messaging_manager, notification_manager
    ):
        """Test feed pagination with before_id."""
        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            owner = auth_manager.register(
                username="groupowner",
                email="owner@example.com",
                password="TestPass123!",
            )
            member1 = auth_manager.register(
                username="member1", email="member1@example.com", password="TestPass123!"
            )
            member2 = auth_manager.register(
                username="member2", email="member2@example.com", password="TestPass123!"
            )

        group = messaging_manager.create_group(
            owner.id, "Test Group", [member1.id, member2.id]
        )

        for i in range(5):
            content = f"<@{member1.id}> message {i}"
            msg = messaging_manager.send_message(owner.id, group.id, content)
            notification_manager.create_notifications_for_message(
                author_id=owner.id,
                message_id=msg.id,
                conversation_id=group.id,
                content=content,
            )

        feed1 = notification_manager.get_notification_feed(member1.id, limit=2)

        assert len(feed1.notifications) == 2

        if len(feed1.notifications) > 0:
            last_id = feed1.notifications[-1].id
            feed2 = notification_manager.get_notification_feed(
                member1.id, limit=2, before_id=last_id
            )

            for notif in feed2.notifications:
                assert notif.id < last_id

    def test_feed_has_more(self, auth_manager, messaging_manager, notification_manager):
        """Test feed has_more flag."""
        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            owner = auth_manager.register(
                username="groupowner",
                email="owner@example.com",
                password="TestPass123!",
            )
            member1 = auth_manager.register(
                username="member1", email="member1@example.com", password="TestPass123!"
            )
            member2 = auth_manager.register(
                username="member2", email="member2@example.com", password="TestPass123!"
            )

        group = messaging_manager.create_group(
            owner.id, "Test Group", [member1.id, member2.id]
        )

        for i in range(5):
            content = f"<@{member1.id}> message {i}"
            msg = messaging_manager.send_message(owner.id, group.id, content)
            notification_manager.create_notifications_for_message(
                author_id=owner.id,
                message_id=msg.id,
                conversation_id=group.id,
                content=content,
            )

        feed = notification_manager.get_notification_feed(member1.id, limit=2)

        assert feed.has_more is True


class TestGetNotifications:
    """Tests for getting notifications list."""

    def test_get_notifications(
        self, auth_manager, messaging_manager, notification_manager
    ):
        """Test getting notifications list."""
        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user1 = auth_manager.register(
                username="user1", email="user1@example.com", password="TestPass123!"
            )
            user2 = auth_manager.register(
                username="user2", email="user2@example.com", password="TestPass123!"
            )

        dm = messaging_manager.create_dm(user1.id, user2.id)

        content = f"<@{user2.id}>"
        msg = messaging_manager.send_message(user1.id, dm.id, content)

        notification_manager.create_notifications_for_message(
            author_id=user1.id,
            message_id=msg.id,
            conversation_id=dm.id,
            content=content,
        )

        notifs = notification_manager.get_notifications(user2.id)

        assert len(notifs) >= 1

    def test_get_unread_only(
        self, auth_manager, messaging_manager, notification_manager
    ):
        """Test getting only unread notifications."""
        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            owner = auth_manager.register(
                username="groupowner",
                email="owner@example.com",
                password="TestPass123!",
            )
            member1 = auth_manager.register(
                username="member1", email="member1@example.com", password="TestPass123!"
            )
            member2 = auth_manager.register(
                username="member2", email="member2@example.com", password="TestPass123!"
            )

        group = messaging_manager.create_group(
            owner.id, "Test Group", [member1.id, member2.id]
        )

        for i in range(3):
            content = f"<@{member1.id}> message {i}"
            msg = messaging_manager.send_message(owner.id, group.id, content)
            notification_manager.create_notifications_for_message(
                author_id=owner.id,
                message_id=msg.id,
                conversation_id=group.id,
                content=content,
            )

        all_notifs = notification_manager.get_notifications(member1.id)
        if len(all_notifs) > 0:
            notification_manager.mark_notification_read(member1.id, all_notifs[0].id)

        unread_notifs = notification_manager.get_notifications(
            member1.id, unread_only=True
        )

        assert len(unread_notifs) < len(all_notifs)

    def test_get_notifications_pagination(
        self, auth_manager, messaging_manager, notification_manager
    ):
        """Test notifications pagination."""
        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            owner = auth_manager.register(
                username="groupowner",
                email="owner@example.com",
                password="TestPass123!",
            )
            member1 = auth_manager.register(
                username="member1", email="member1@example.com", password="TestPass123!"
            )
            member2 = auth_manager.register(
                username="member2", email="member2@example.com", password="TestPass123!"
            )

        group = messaging_manager.create_group(
            owner.id, "Test Group", [member1.id, member2.id]
        )

        for i in range(5):
            content = f"<@{member1.id}> message {i}"
            msg = messaging_manager.send_message(owner.id, group.id, content)
            notification_manager.create_notifications_for_message(
                author_id=owner.id,
                message_id=msg.id,
                conversation_id=group.id,
                content=content,
            )

        page1 = notification_manager.get_notifications(member1.id, limit=2)

        assert len(page1) == 2

        if len(page1) > 0:
            page2 = notification_manager.get_notifications(
                member1.id, limit=2, before_id=page1[-1].id
            )

            for notif in page2:
                assert notif.id < page1[-1].id


class TestDeleteNotification:
    """Tests for deleting notifications."""

    def test_delete_notification(
        self, auth_manager, messaging_manager, notification_manager
    ):
        """Test deleting a notification."""
        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user1 = auth_manager.register(
                username="user1", email="user1@example.com", password="TestPass123!"
            )
            user2 = auth_manager.register(
                username="user2", email="user2@example.com", password="TestPass123!"
            )

        dm = messaging_manager.create_dm(user1.id, user2.id)

        content = f"<@{user2.id}>"
        msg = messaging_manager.send_message(user1.id, dm.id, content)

        notifs = notification_manager.create_notifications_for_message(
            author_id=user1.id,
            message_id=msg.id,
            conversation_id=dm.id,
            content=content,
        )

        assert len(notifs) == 1

        result = notification_manager.delete_notification(user2.id, notifs[0].id)

        assert result is True

        notif = notification_manager.get_notification(notifs[0].id)
        assert notif is None

    def test_delete_nonexistent_notification(self, notification_manager):
        """Test deleting nonexistent notification raises error."""
        from src.core.notifications import NotificationNotFoundError

        with pytest.raises(NotificationNotFoundError):
            notification_manager.delete_notification(1, 999999999)

    def test_delete_other_users_notification(
        self, auth_manager, messaging_manager, notification_manager
    ):
        """Test cannot delete another user's notification."""
        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user1 = auth_manager.register(
                username="user1", email="user1@example.com", password="TestPass123!"
            )
            user2 = auth_manager.register(
                username="user2", email="user2@example.com", password="TestPass123!"
            )

        dm = messaging_manager.create_dm(user1.id, user2.id)

        content = f"<@{user2.id}>"
        msg = messaging_manager.send_message(user1.id, dm.id, content)

        notifs = notification_manager.create_notifications_for_message(
            author_id=user1.id,
            message_id=msg.id,
            conversation_id=dm.id,
            content=content,
        )

        from src.core.notifications import NotificationNotFoundError

        with pytest.raises(NotificationNotFoundError):
            notification_manager.delete_notification(user1.id, notifs[0].id)
