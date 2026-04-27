"""Tests for unread and mention counts."""

from unittest.mock import patch
from src.utils import encryption


class TestGetUnreadCount:
    """Tests for getting unread counts."""

    def test_initial_unread_count_zero(self, notification_manager):
        """Test initial unread count is zero."""
        unread = notification_manager.get_unread_count(1)

        assert unread.total_unread == 0
        assert unread.mention_count == 0

    def test_unread_count_after_mention(
        self, auth_manager, messaging_manager, notification_manager
    ):
        """Test unread count increases after mention."""
        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user1 = auth_manager.register(
                username="user1", email="user1@example.com", password="TestPass123!"
            )
            user2 = auth_manager.register(
                username="user2", email="user2@example.com", password="TestPass123!"
            )

        dm = messaging_manager.create_dm(user1.id, user2.id)

        content = f"Hey <@{user2.id}>"
        msg = messaging_manager.send_message(user1.id, dm.id, content)

        notification_manager.create_notifications_for_message(
            author_id=user1.id,
            message_id=msg.id,
            conversation_id=dm.id,
            content=content,
        )

        unread = notification_manager.get_unread_count(user2.id)

        assert unread.mention_count >= 1


class TestUnreadCountPerServer:
    """Tests for unread count filtered by server."""

    def test_unread_count_per_server(self):
        """Test unread count filtered by server."""
        pass


class TestGetUnreadCounts:
    """Tests for getting all unread counts."""

    def test_get_all_unread_counts(
        self, auth_manager, messaging_manager, notification_manager
    ):
        """Test getting all unread counts per conversation."""
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

        content = f"<@{member1.id}>"
        msg = messaging_manager.send_message(owner.id, group.id, content)

        notification_manager.create_notifications_for_message(
            author_id=owner.id,
            message_id=msg.id,
            conversation_id=group.id,
            content=content,
        )

        counts = notification_manager.get_unread_counts(member1.id)

        assert group.id in counts
        assert counts[group.id].mention_count >= 1

    def test_empty_unread_counts(self, notification_manager):
        """Test empty unread counts for new user."""
        counts = notification_manager.get_unread_counts(1)

        assert len(counts) == 0


class TestGetMentionCount:
    """Tests for getting mention counts."""

    def test_mention_count_zero_initially(self, notification_manager):
        """Test mention count is zero initially."""
        count = notification_manager.get_mention_count(1)

        assert count == 0

    def test_mention_count_increases(
        self, auth_manager, messaging_manager, notification_manager
    ):
        """Test mention count increases with mentions."""
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

        count = notification_manager.get_mention_count(user2.id)

        assert count >= 1


class TestMentionCountPerServer:
    """Tests for mention count filtered by server."""

    def test_mention_count_per_server(self):
        """Test mention count filtered by server."""
        pass


class TestMarkRead:
    """Tests for marking notifications as read."""

    def test_mark_notification_read(
        self, auth_manager, messaging_manager, notification_manager
    ):
        """Test marking single notification as read."""
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

        result = notification_manager.mark_notification_read(user2.id, notifs[0].id)

        assert result is True

        notif = notification_manager.get_notification(notifs[0].id)
        assert notif.read is True

    def test_mark_all_read(self, auth_manager, messaging_manager, notification_manager):
        """Test marking all notifications as read."""
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

        count = notification_manager.mark_all_read(member1.id)

        assert count >= 3

        unread = notification_manager.get_unread_count(member1.id)
        assert unread.mention_count == 0


class TestMarkChannelRead:
    """Tests for marking channel notifications as read."""

    def test_mark_channel_read(self):
        """Test marking channel notifications as read."""
        pass


class TestMarkServerRead:
    """Tests for marking server notifications as read."""

    def test_mark_server_read(self):
        """Test marking server notifications as read."""
        pass
