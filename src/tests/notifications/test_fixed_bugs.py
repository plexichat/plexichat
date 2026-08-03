"""
Tests for bugs that were fixed in the notifications system.
"""

import pytest
from src.core.notifications import (
    NotificationNotFoundError,
    MentionType,
    NotificationLevel,
)
from unittest.mock import patch
from src.utils import encryption


class TestContentPreviewEncryptionFallback:
    """Tests for content preview when encryption fails."""

    def test_content_preview_not_empty_when_encryption_fails(
        self, auth_manager, messaging_manager, notification_manager
    ):
        """Test that content preview is stored even when encryption fails."""
        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user1 = auth_manager.register(
                username="testuser1", email="user1@example.com", password="TestPass123!"
            )
            user2 = auth_manager.register(
                username="testuser2", email="user2@example.com", password="TestPass123!"
            )

        dm = messaging_manager.create_dm(user1.id, user2.id)

        content = f"Hey <@{user2.id}> this is a test message"
        msg = messaging_manager.send_message(user1.id, dm.id, content)

        # Mock encryption to fail
        with patch(
            "src.core.notifications.manager.creator.encrypt_data",
            side_effect=Exception("Encryption failed"),
        ):
            notifs = notification_manager.create_notifications_for_message(
                author_id=user1.id,
                message_id=msg.id,
                conversation_id=dm.id,
                content=content,
            )

        assert len(notifs) == 1
        # Content preview should include the mention since truncation happens on full content
        assert "test message" in notifs[0].content_preview
        assert notifs[0].content_preview != ""


class TestAtHereRegex:
    """Tests for @here mention parsing regex fix."""

    def test_partial_here_not_parsed(self, notification_manager):
        """Test that partial @here (like @heretext) is not parsed."""
        content = "@heretext @hereother"
        mentions = notification_manager.parse_mentions(content)

        assert len(mentions) == 0

    def test_valid_here_parsed(self, notification_manager):
        """Test that valid @here is parsed."""
        content = "@here hello"
        mentions = notification_manager.parse_mentions(content)

        assert len(mentions) == 1
        assert mentions[0].mention_type == MentionType.HERE

    def test_here_with_punctuation(self, notification_manager):
        """Test @here with punctuation."""
        content = "Hey @here! What's up?"
        mentions = notification_manager.parse_mentions(content)

        assert len(mentions) == 1
        assert mentions[0].mention_type == MentionType.HERE
        assert mentions[0].raw_text == "@here"


class TestEveryoneValidationWithServerId:
    """Tests for @everyone/@here validation when server_id provided but servers module missing."""

    def test_everyone_in_dm_still_rejected(self, notification_manager):
        """Test @everyone in DM (no server_id) is still rejected."""
        content = "@everyone"
        mentions = notification_manager.parse_mentions(content)
        validated = notification_manager.validate_mentions(1, mentions)

        assert len(validated) == 1
        assert validated[0].valid is False
        assert "dm" in validated[0].error.lower()

    def test_everyone_with_server_id_but_no_servers_module(self, notification_manager):
        """Test @everyone with server_id but servers module missing - uses config."""
        # This test checks the logic path when servers module is None but server_id is provided
        # The config allow_everyone_when_servers_missing defaults to False
        content = "@everyone"
        mentions = notification_manager.parse_mentions(content)
        validated = notification_manager.validate_mentions(
            1, mentions, server_id=123, channel_id=456
        )

        assert len(validated) == 1
        assert validated[0].valid is False
        assert "servers module not loaded" in validated[0].error.lower()


class TestChannelMentions:
    """Tests for channel mention handling."""

    def test_channel_mention_parsed(self, notification_manager):
        """Test that channel mentions are parsed."""
        content = "Check <#123456789>"
        mentions = notification_manager.parse_mentions(content)

        assert len(mentions) == 1
        assert mentions[0].mention_type == MentionType.CHANNEL
        assert mentions[0].target_id == 123456789

    def test_channel_mention_validated(self, notification_manager):
        """Test that channel mentions are validated."""
        mentions = [
            type(
                "Mention",
                (),
                {
                    "mention_type": MentionType.CHANNEL,
                    "target_id": 999999999,
                    "raw_text": "<#999999999>",
                    "start_pos": 0,
                    "end_pos": 11,
                    "valid": True,
                },
            )()
        ]

        # Channel doesn't exist, should be invalid
        validated = notification_manager.validate_mentions(1, mentions, channel_id=123)
        assert len(validated) == 1
        assert validated[0].valid is False
        assert "channel not found" in validated[0].error.lower()


class TestThreadIdSupport:
    """Tests for thread_id support in mark read methods."""

    def test_mark_thread_read(
        self, auth_manager, messaging_manager, notification_manager
    ):
        """Test marking thread notifications as read."""
        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user1 = auth_manager.register(
                username="testuser3", email="user3@example.com", password="TestPass123!"
            )
            user2 = auth_manager.register(
                username="testuser4", email="user4@example.com", password="TestPass123!"
            )

        dm = messaging_manager.create_dm(user1.id, user2.id)
        thread_id = 999999999

        content = f"<@{user2.id}> test"
        msg = messaging_manager.send_message(user1.id, dm.id, content)

        notifs = notification_manager.create_notifications_for_message(
            author_id=user1.id,
            message_id=msg.id,
            conversation_id=dm.id,
            content=content,
            thread_id=thread_id,
        )

        assert len(notifs) == 1
        assert notifs[0].thread_id == thread_id

        # Mark thread as read
        count = notification_manager.mark_thread_read(user2.id, thread_id)
        assert count == 1

        # Verify notification is read
        notif = notification_manager.get_notification(notifs[0].id)
        assert notif.read is True


class TestNotificationFeedOptimization:
    """Tests for optimized get_notification_feed."""

    def test_feed_returns_correct_counts(
        self, auth_manager, messaging_manager, notification_manager
    ):
        """Test that feed returns correct total and unread counts."""
        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user1 = auth_manager.register(
                username="feeduser1", email="feed1@example.com", password="TestPass123!"
            )
            user2 = auth_manager.register(
                username="feeduser2", email="feed2@example.com", password="TestPass123!"
            )

        dm = messaging_manager.create_dm(user1.id, user2.id)

        # Create multiple notifications
        for i in range(3):
            content = f"<@{user2.id}> message {i}"
            msg = messaging_manager.send_message(user1.id, dm.id, content)
            notification_manager.create_notifications_for_message(
                author_id=user1.id,
                message_id=msg.id,
                conversation_id=dm.id,
                content=content,
            )

        feed = notification_manager.get_notification_feed(user2.id)

        assert feed.total_count == 3
        assert feed.unread_count == 3
        assert len(feed.notifications) == 3
        assert feed.has_more is False


class TestConversationIdRequired:
    """Tests for conversation_id being required."""

    def test_create_notifications_requires_conversation_id(self, notification_manager):
        """Test that create_notifications_for_message requires conversation_id."""
        with pytest.raises(ValueError, match="conversation_id must be provided"):
            notification_manager.create_notifications_for_message(
                message_id=123,
                author_id=456,
                conversation_id=None,
                content="test",
            )


class TestCacheKeyWithServerIdZero:
    """Tests for cache key handling with server_id=0."""

    def test_server_id_zero_not_treated_as_global(
        self, auth_manager, notification_manager
    ):
        """Test that server_id=0 is not treated as global settings."""
        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user1 = auth_manager.register(
                username="cacheuser1",
                email="cache1@example.com",
                password="TestPass123!",
            )

        # Set settings for server_id=0
        settings = notification_manager.update_notification_settings(
            user1.id, server_id=0, level=NotificationLevel.ONLY_MENTIONS
        )

        assert settings.server_id == 0
        assert settings.level == NotificationLevel.ONLY_MENTIONS

        # Get settings back - should not return global settings
        retrieved = notification_manager.get_notification_settings(
            user1.id, server_id=0
        )
        assert retrieved.server_id == 0
        assert retrieved.level == NotificationLevel.ONLY_MENTIONS


class TestMarkChannelReadUpdatesUnreadCounts:
    """Tests for mark_channel_read correctly updating unread counts."""

    def test_mark_channel_read_decrements_unread(
        self, users_with_server, messaging_manager, notification_manager
    ):
        """Test that mark_channel_read decrements unread counts per conversation."""
        owner, member1, member2, server, channel, servers, messaging, notifications = (
            users_with_server
        )

        # member1 sends messages mentioning member2 in the channel
        channel_id = channel.id
        server_id = server.id if hasattr(server, "id") else server

        # Create real conversation + messages to satisfy FK constraints
        dm = messaging_manager.create_dm(member1.id, member2.id)

        # Create multiple notifications in same channel
        for i in range(3):
            content = f"<@{member2.id}> message {i}"
            msg = messaging_manager.send_message(member1.id, dm.id, content)
            notification_manager.create_notifications_for_message(
                author_id=member1.id,
                message_id=msg.id,
                conversation_id=dm.id,
                content=content,
                server_id=server_id,
                channel_id=channel_id,
            )

        # Check unread count before
        unread_before = notification_manager.get_unread_count(member2.id)
        assert unread_before.unread_count == 3
        assert unread_before.mention_count == 3

        # Mark channel as read
        count = notification_manager.mark_channel_read(member2.id, channel_id)
        assert count == 3

        # Check unread count after
        unread_after = notification_manager.get_unread_count(member2.id)
        assert unread_after.unread_count == 0
        assert unread_after.mention_count == 0


class TestMarkServerReadUpdatesUnreadCounts:
    """Tests for mark_server_read correctly updating unread counts."""

    def test_mark_server_read_decrements_unread(
        self, users_with_server, messaging_manager, notification_manager
    ):
        """Test that mark_server_read decrements unread counts per conversation."""
        owner, member1, member2, server, channel, servers, messaging, notifications = (
            users_with_server
        )

        server_id = server.id if hasattr(server, "id") else server
        channel_id = channel.id

        # Create real conversation + messages to satisfy FK constraints
        dm = messaging_manager.create_dm(member1.id, member2.id)

        # member1 sends messages mentioning member2 in the channel
        for i in range(3):
            content = f"<@{member2.id}> message {i}"
            msg = messaging_manager.send_message(member1.id, dm.id, content)
            notification_manager.create_notifications_for_message(
                author_id=member1.id,
                message_id=msg.id,
                conversation_id=dm.id,
                content=content,
                server_id=server_id,
                channel_id=channel_id,
            )

        # Check unread count before
        unread_before = notification_manager.get_unread_count(
            member2.id, server_id=server_id
        )
        assert unread_before.unread_count == 3
        assert unread_before.mention_count == 3

        # Mark server as read
        count = notification_manager.mark_server_read(member2.id, server_id)
        assert count == 3

        # Check unread count after
        unread_after = notification_manager.get_unread_count(
            member2.id, server_id=server_id
        )
        assert unread_after.unread_count == 0
        assert unread_after.mention_count == 0
