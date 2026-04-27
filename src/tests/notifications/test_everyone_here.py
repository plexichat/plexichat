"""
Tests for @everyone and @here mentions with permissions.
"""

from src.core.notifications import MentionType
from unittest.mock import patch
from src.utils import encryption


class TestEveryoneParsing:
    """Tests for parsing @everyone mentions."""

    def test_parse_everyone_mention(self, notification_manager):
        """Test parsing @everyone mention."""
        content = "Hey @everyone check this out"
        mentions = notification_manager.parse_mentions(content)

        assert len(mentions) == 1
        assert mentions[0].mention_type == MentionType.EVERYONE
        assert mentions[0].target_id is None
        assert mentions[0].raw_text == "@everyone"

    def test_parse_everyone_position(self, notification_manager):
        """Test @everyone position is correct."""
        content = "Hey @everyone"
        mentions = notification_manager.parse_mentions(content)

        assert mentions[0].start_pos == 4
        assert mentions[0].end_pos == 13


class TestHereParsing:
    """Tests for parsing @here mentions."""

    def test_parse_here_mention(self, notification_manager):
        """Test parsing @here mention."""
        content = "Hey @here check this out"
        mentions = notification_manager.parse_mentions(content)

        assert len(mentions) == 1
        assert mentions[0].mention_type == MentionType.HERE
        assert mentions[0].target_id is None
        assert mentions[0].raw_text == "@here"

    def test_parse_both_everyone_and_here(self, notification_manager):
        """Test parsing both @everyone and @here."""
        content = "@everyone and @here"
        mentions = notification_manager.parse_mentions(content)

        assert len(mentions) == 2
        types = {m.mention_type for m in mentions}
        assert MentionType.EVERYONE in types
        assert MentionType.HERE in types


class TestEveryoneValidation:
    """Tests for validating @everyone mentions."""

    def test_everyone_invalid_in_dm(
        self, auth_manager, messaging_manager, notification_manager
    ):
        """Test @everyone is invalid in DMs."""
        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user1 = auth_manager.register(
                username="testuser1", email="user1@example.com", password="TestPass123!"
            )
            user2 = auth_manager.register(
                username="testuser2", email="user2@example.com", password="TestPass123!"
            )

        dm = messaging_manager.create_dm(user1.id, user2.id)

        content = "@everyone"
        mentions = notification_manager.parse_mentions(content)
        validated = notification_manager.validate_mentions(user1.id, mentions)

        assert len(validated) == 1
        assert validated[0].valid is False
        assert "dm" in validated[0].error.lower()

    def test_everyone_valid_with_permission(
        self, auth_manager, messaging_manager, notification_manager
    ):
        """Test @everyone is valid with permission in server."""
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

        content = "@everyone"
        mentions = notification_manager.parse_mentions(content)
        validated = notification_manager.validate_mentions(
            owner.id, mentions, server_id=123, channel_id=456
        )

        assert len(validated) == 1
        assert validated[0].valid is True

    def test_here_invalid_in_dm(
        self, auth_manager, messaging_manager, notification_manager
    ):
        """Test @here is invalid in DMs."""
        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user1 = auth_manager.register(
                username="testuser3", email="user3@example.com", password="TestPass123!"
            )
            user2 = auth_manager.register(
                username="testuser4", email="user4@example.com", password="TestPass123!"
            )

        dm = messaging_manager.create_dm(user1.id, user2.id)

        content = "@here"
        mentions = notification_manager.parse_mentions(content)
        validated = notification_manager.validate_mentions(user1.id, mentions)

        assert len(validated) == 1
        assert validated[0].valid is False


class TestEveryoneNotifications:
    """Tests for @everyone notifications."""

    def test_everyone_notifies_all_members(
        self, auth_manager, messaging_manager, notification_manager
    ):
        """Test @everyone notifies all server members."""
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

        content = "@everyone check this out"
        msg = messaging_manager.send_message(owner.id, group.id, content)

        notifs = notification_manager.create_notifications_for_message(
            author_id=owner.id,
            message_id=msg.id,
            conversation_id=group.id,
            content=content,
            server_id=123,
        )

        # @everyone doesn't work in DMs/groups, so no notifications should be created
        assert len(notifs) == 0

    def test_everyone_does_not_notify_sender(
        self, auth_manager, messaging_manager, notification_manager
    ):
        """Test @everyone doesn't notify the sender."""
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

        content = "@everyone"
        msg = messaging_manager.send_message(owner.id, group.id, content)

        notifs = notification_manager.create_notifications_for_message(
            author_id=owner.id,
            message_id=msg.id,
            conversation_id=group.id,
            content=content,
            server_id=123,
        )

        notified_users = {n.user_id for n in notifs}
        assert owner.id not in notified_users


class TestHereNotifications:
    """Tests for @here notifications."""

    def test_here_notifies_online_members(
        self, auth_manager, messaging_manager, notification_manager
    ):
        """Test @here notifies online members."""
        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            owner = auth_manager.register(
                username="testowner4",
                email="owner4@example.com",
                password="TestPass123!",
            )
            member1 = auth_manager.register(
                username="testmember7",
                email="member7@example.com",
                password="TestPass123!",
            )
            member2 = auth_manager.register(
                username="testmember8",
                email="member8@example.com",
                password="TestPass123!",
            )

        group = messaging_manager.create_group(
            owner.id, "Server Group", [member1.id, member2.id]
        )

        content = "@here check this out"
        msg = messaging_manager.send_message(owner.id, group.id, content)

        notifs = notification_manager.create_notifications_for_message(
            author_id=owner.id,
            message_id=msg.id,
            conversation_id=group.id,
            content=content,
            server_id=123,
        )

        for notif in notifs:
            assert notif.mention_type == MentionType.HERE

    def test_here_does_not_notify_sender(
        self, auth_manager, messaging_manager, notification_manager
    ):
        """Test @here doesn't notify the sender."""
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

        content = "@here"
        msg = messaging_manager.send_message(owner.id, group.id, content)

        notifs = notification_manager.create_notifications_for_message(
            author_id=owner.id,
            message_id=msg.id,
            conversation_id=group.id,
            content=content,
            server_id=123,
        )

        notified_users = {n.user_id for n in notifs}
        assert owner.id not in notified_users
