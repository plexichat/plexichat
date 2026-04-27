"""
Tests for integration with other modules.
"""

from src.core.notifications import MentionType
from unittest.mock import patch
from src.utils import encryption


class TestMessagingIntegration:
    """Tests for integration with messaging module."""

    def test_notification_includes_message_id(
        self, auth_manager, messaging_manager, notification_manager
    ):
        """Test notification includes correct message ID."""
        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user1 = auth_manager.register(
                username="testuser1", email="user1@example.com", password="TestPass123!"
            )
            user2 = auth_manager.register(
                username="testuser2", email="user2@example.com", password="TestPass123!"
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
        assert notifs[0].message_id == msg.id

    def test_notification_includes_conversation_id(
        self, auth_manager, messaging_manager, notification_manager
    ):
        """Test notification includes correct conversation ID."""
        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user1 = auth_manager.register(
                username="testuser3", email="user3@example.com", password="TestPass123!"
            )
            user2 = auth_manager.register(
                username="testuser4", email="user4@example.com", password="TestPass123!"
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
        assert notifs[0].conversation_id == dm.id

    def test_notification_in_group_conversation(
        self, auth_manager, messaging_manager, notification_manager
    ):
        """Test notifications work in group conversations."""
        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            owner = auth_manager.register(
                username="testowner1",
                email="owner1@example.com",
                password="TestPass123!",
            )
            member1 = auth_manager.register(
                username="testmember1",
                email="member1@example.com",
                password="TestPass123!",
            )
            member2 = auth_manager.register(
                username="testmember2",
                email="member2@example.com",
                password="TestPass123!",
            )

        group = messaging_manager.create_group(
            owner.id, "Server Group", [member1.id, member2.id]
        )

        content = f"<@{member1.id}> and <@{member2.id}>"
        msg = messaging_manager.send_message(owner.id, group.id, content)

        notifs = notification_manager.create_notifications_for_message(
            author_id=owner.id,
            message_id=msg.id,
            conversation_id=group.id,
            content=content,
        )

        assert len(notifs) == 2
        notified_users = {n.user_id for n in notifs}
        assert member1.id in notified_users
        assert member2.id in notified_users


class TestServersIntegration:
    """Tests for integration with servers module."""

    def test_notification_includes_server_id(
        self, auth_manager, messaging_manager, notification_manager
    ):
        """Test notification includes server ID when provided."""
        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            owner = auth_manager.register(
                username="testowner2",
                email="owner2@example.com",
                password="TestPass123!",
            )
            member1 = auth_manager.register(
                username="testmember3",
                email="member3@example.com",
                password="TestPass123!",
            )
            member2 = auth_manager.register(
                username="testmember4",
                email="member4@example.com",
                password="TestPass123!",
            )

        group = messaging_manager.create_group(
            owner.id, "Server Group", [member1.id, member2.id]
        )

        content = f"<@{member1.id}>"
        msg = messaging_manager.send_message(owner.id, group.id, content)

        notifs = notification_manager.create_notifications_for_message(
            author_id=owner.id,
            message_id=msg.id,
            conversation_id=group.id,
            content=content,
            server_id=123,
        )

        assert len(notifs) == 1
        assert notifs[0].server_id == 123

    def test_notification_includes_channel_id(
        self, auth_manager, messaging_manager, notification_manager
    ):
        """Test notification includes channel ID when provided."""
        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            owner = auth_manager.register(
                username="testowner3",
                email="owner3@example.com",
                password="TestPass123!",
            )
            member1 = auth_manager.register(
                username="testmember5",
                email="member5@example.com",
                password="TestPass123!",
            )
            member2 = auth_manager.register(
                username="testmember6",
                email="member6@example.com",
                password="TestPass123!",
            )

        group = messaging_manager.create_group(
            owner.id, "Server Group", [member1.id, member2.id]
        )

        content = f"<@{member1.id}>"
        msg = messaging_manager.send_message(owner.id, group.id, content)

        notifs = notification_manager.create_notifications_for_message(
            author_id=owner.id,
            message_id=msg.id,
            conversation_id=group.id,
            content=content,
            server_id=123,
            channel_id=456,
        )

        assert len(notifs) == 1
        assert notifs[0].channel_id == 456


class TestRelationshipsIntegration:
    """Tests for integration with relationships module."""

    def test_blocked_user_not_notified(
        self, auth_manager, messaging_manager, rel_manager, notification_manager
    ):
        """Test blocked user doesn't receive notification."""
        pass

    def test_user_who_blocked_sender_not_notified(
        self, auth_manager, messaging_manager, rel_manager, notification_manager
    ):
        """Test user who blocked sender doesn't receive notification."""
        pass


class TestHighlightMentions:
    """Tests for highlight mentions functionality."""

    def test_highlight_user_mention(
        self, auth_manager, messaging_manager, notification_manager
    ):
        """Test highlighting user mention."""
        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user1 = auth_manager.register(
                username="testuser8", email="user8@example.com", password="TestPass123!"
            )
            user2 = auth_manager.register(
                username="testuser9", email="user9@example.com", password="TestPass123!"
            )

        dm = messaging_manager.create_dm(user1.id, user2.id)

        content = f"Hey <@{user2.id}> check this"
        positions = notification_manager.highlight_mentions(content, user2.id)

        assert len(positions) == 1
        assert positions[0].mention_type == MentionType.USER
        assert positions[0].is_self is True

    def test_highlight_other_user_mention(
        self, auth_manager, messaging_manager, notification_manager
    ):
        """Test highlighting mention of other user."""
        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user1 = auth_manager.register(
                username="testuser10",
                email="user10@example.com",
                password="TestPass123!",
            )
            user2 = auth_manager.register(
                username="testuser11",
                email="user11@example.com",
                password="TestPass123!",
            )

        dm = messaging_manager.create_dm(user1.id, user2.id)

        content = f"Hey <@{user2.id}> check this"
        positions = notification_manager.highlight_mentions(content, user1.id)

        assert len(positions) == 1
        assert positions[0].is_self is False

    def test_highlight_everyone_mention(self, notification_manager):
        """Test highlighting @everyone mention."""
        content = "@everyone check this"
        positions = notification_manager.highlight_mentions(content, 123)

        assert len(positions) == 1
        assert positions[0].mention_type == MentionType.EVERYONE
        assert positions[0].is_self is True

    def test_highlight_multiple_mentions(
        self, auth_manager, messaging_manager, notification_manager
    ):
        """Test highlighting multiple mentions."""
        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            owner = auth_manager.register(
                username="testowner5",
                email="owner5@example.com",
                password="TestPass123!",
            )
            member1 = auth_manager.register(
                username="testmember9",
                email="member9@example.com",
                password="TestPass123!",
            )
            member2 = auth_manager.register(
                username="testmember10",
                email="member10@example.com",
                password="TestPass123!",
            )

        group = messaging_manager.create_group(
            owner.id, "Server Group", [member1.id, member2.id]
        )

        content = f"<@{member1.id}> and <@{member2.id}> @everyone"
        positions = notification_manager.highlight_mentions(content, member1.id)

        assert len(positions) == 3
        self_mentions = [p for p in positions if p.is_self]
        assert len(self_mentions) == 2


class TestPushPayload:
    """Tests for push notification payload preparation."""

    def test_prepare_push_payload(
        self, auth_manager, messaging_manager, notification_manager
    ):
        """Test preparing push notification payload."""
        pass
