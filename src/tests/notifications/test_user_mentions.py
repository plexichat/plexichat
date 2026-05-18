"""Tests for @user mention parsing and notifications."""

from unittest.mock import patch
from src.utils import encryption
from src.core.notifications import MentionType


class TestUserMentionParsing:
    """Tests for parsing @user mentions."""

    def test_parse_single_user_mention(self, notification_manager):
        """Test parsing a single user mention."""
        content = "Hello <@123456789>"
        mentions = notification_manager.parse_mentions(content)

        assert len(mentions) == 1
        assert mentions[0].mention_type == MentionType.USER
        assert mentions[0].target_id == 123456789
        assert mentions[0].raw_text == "<@123456789>"

    def test_parse_multiple_user_mentions(self, notification_manager):
        """Test parsing multiple user mentions."""
        content = "Hey <@111> and <@222> check this out"
        mentions = notification_manager.parse_mentions(content)

        assert len(mentions) == 2
        assert mentions[0].target_id == 111
        assert mentions[1].target_id == 222

    def test_parse_user_mention_positions(self, notification_manager):
        """Test mention positions are correct."""
        content = "Hello <@123>"
        mentions = notification_manager.parse_mentions(content)

        assert mentions[0].start_pos == 6
        assert mentions[0].end_pos == 12

    def test_parse_no_mentions(self, notification_manager):
        """Test parsing content with no mentions."""
        content = "Hello world, no mentions here"
        mentions = notification_manager.parse_mentions(content)

        assert len(mentions) == 0

    def test_parse_empty_content(self, notification_manager):
        """Test parsing empty content."""
        mentions = notification_manager.parse_mentions("")
        assert len(mentions) == 0

        mentions = notification_manager.parse_mentions(None)
        assert len(mentions) == 0


class TestUserMentionValidation:
    """Tests for validating @user mentions."""

    def test_validate_existing_user(self, auth_manager, notification_manager):
        """Test validating mention of existing user."""
        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user1 = auth_manager.register(
                username="user1", email="user1@example.com", password="TestPass123!"
            )
            user2 = auth_manager.register(
                username="user2", email="user2@example.com", password="TestPass123!"
            )

        content = f"Hey <@{user2.id}>"
        mentions = notification_manager.parse_mentions(content)
        validated = notification_manager.validate_mentions(user1.id, mentions)

        assert len(validated) == 1
        assert validated[0].valid is True

    def test_validate_nonexistent_user(self, notification_manager):
        """Test validating mention of nonexistent user."""
        content = "<@999999999999>"
        mentions = notification_manager.parse_mentions(content)
        validated = notification_manager.validate_mentions(1, mentions)

        assert len(validated) == 1
        assert validated[0].valid is False
        assert "not found" in validated[0].error.lower()


class TestUserMentionNotifications:
    """Tests for creating notifications from @user mentions."""

    def test_create_notification_for_user_mention(
        self, auth_manager, messaging_manager, notification_manager
    ):
        """Test notification is created for mentioned user."""
        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user1 = auth_manager.register(
                username="user1", email="user1@example.com", password="TestPass123!"
            )
            user2 = auth_manager.register(
                username="user2", email="user2@example.com", password="TestPass123!"
            )

        dm = messaging_manager.create_dm(user1.id, user2.id)

        content = f"Hey <@{user2.id}> check this out"
        msg = messaging_manager.send_message(user1.id, dm.id, content)

        notifs = notification_manager.create_notifications_for_message(
            author_id=user1.id,
            message_id=msg.id,
            conversation_id=dm.id,
            content=content,
        )

        assert len(notifs) == 1
        assert notifs[0].user_id == user2.id
        assert notifs[0].author_id == user1.id
        assert notifs[0].mention_type == MentionType.USER

    def test_no_notification_for_self_mention(
        self, auth_manager, messaging_manager, notification_manager
    ):
        """Test no notification when user mentions themselves."""
        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user1 = auth_manager.register(
                username="user1", email="user1@example.com", password="TestPass123!"
            )
            user2 = auth_manager.register(
                username="user2", email="user2@example.com", password="TestPass123!"
            )

        dm = messaging_manager.create_dm(user1.id, user2.id)

        content = f"I am <@{user1.id}>"
        msg = messaging_manager.send_message(user1.id, dm.id, content)

        notifs = notification_manager.create_notifications_for_message(
            author_id=user1.id,
            message_id=msg.id,
            conversation_id=dm.id,
            content=content,
        )

        assert len(notifs) == 0

    def test_notification_content_preview(
        self, auth_manager, messaging_manager, notification_manager
    ):
        """Test notification includes content preview."""
        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user1 = auth_manager.register(
                username="user1", email="user1@example.com", password="TestPass123!"
            )
            user2 = auth_manager.register(
                username="user2", email="user2@example.com", password="TestPass123!"
            )

        dm = messaging_manager.create_dm(user1.id, user2.id)

        content = f"Hey <@{user2.id}> this is a test message"
        msg = messaging_manager.send_message(user1.id, dm.id, content)

        notifs = notification_manager.create_notifications_for_message(
            author_id=user1.id,
            message_id=msg.id,
            conversation_id=dm.id,
            content=content,
        )

        assert len(notifs) == 1
        assert "test message" in notifs[0].content_preview

    def test_multiple_mentions_same_user(
        self, auth_manager, messaging_manager, notification_manager
    ):
        """Test only one notification for multiple mentions of same user."""
        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user1 = auth_manager.register(
                username="user1", email="user1@example.com", password="TestPass123!"
            )
            user2 = auth_manager.register(
                username="user2", email="user2@example.com", password="TestPass123!"
            )

        dm = messaging_manager.create_dm(user1.id, user2.id)

        content = f"<@{user2.id}> hey <@{user2.id}> hello <@{user2.id}>"
        msg = messaging_manager.send_message(user1.id, dm.id, content)

        notifs = notification_manager.create_notifications_for_message(
            author_id=user1.id,
            message_id=msg.id,
            conversation_id=dm.id,
            content=content,
        )

        assert len(notifs) == 1
        assert notifs[0].user_id == user2.id

    def test_mention_multiple_users(
        self, auth_manager, messaging_manager, notification_manager
    ):
        """Test notifications for multiple mentioned users."""
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

        content = f"Hey <@{member1.id}> and <@{member2.id}>"
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
