"""
Tests for edge cases and error handling.
"""

import pytest
from src.core.notifications import (
    NotificationNotFoundError,
    MentionType,
)
from unittest.mock import patch
from src.utils import encryption


class TestInvalidMentions:
    """Tests for invalid mention handling."""

    def test_malformed_user_mention(self, notification_manager):
        """Test malformed user mention is not parsed."""
        content = "<@abc> <@> <@ 123>"
        mentions = notification_manager.parse_mentions(content)

        assert len(mentions) == 0

    def test_malformed_role_mention(self, notification_manager):
        """Test malformed role mention is not parsed."""
        content = "<@&abc> <@&> <@& 123>"
        mentions = notification_manager.parse_mentions(content)

        assert len(mentions) == 0

    def test_malformed_channel_mention(self, notification_manager):
        """Test malformed channel mention is not parsed."""
        content = "<#abc> <#> <# 123>"
        mentions = notification_manager.parse_mentions(content)

        assert len(mentions) == 0

    def test_partial_everyone_mention(self, notification_manager):
        """Test partial @everyone is not parsed."""
        content = "@every @everyon everyone@"
        mentions = notification_manager.parse_mentions(content)

        assert len(mentions) == 0

    def test_partial_here_mention(self, notification_manager):
        """Test partial @here is not parsed."""
        content = "@her @her here@"
        mentions = notification_manager.parse_mentions(content)

        assert len(mentions) == 0


class TestNotificationErrors:
    """Tests for notification error handling."""

    def test_mark_nonexistent_notification_read(self, notification_manager):
        """Test marking nonexistent notification as read raises error."""
        with pytest.raises(NotificationNotFoundError):
            notification_manager.mark_notification_read(123, 999999999)

    def test_mark_other_users_notification_read(
        self, auth_manager, messaging_manager, notification_manager
    ):
        """Test cannot mark another user's notification as read."""
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

        with pytest.raises(NotificationNotFoundError):
            notification_manager.mark_notification_read(user1.id, notifs[0].id)

    def test_delete_nonexistent_notification(self, notification_manager):
        """Test deleting nonexistent notification raises error."""
        with pytest.raises(NotificationNotFoundError):
            notification_manager.delete_notification(123, 999999999)


class TestEmptyContent:
    """Tests for empty or null content handling."""

    def test_empty_string_content(self, notification_manager):
        """Test parsing empty string content."""
        mentions = notification_manager.parse_mentions("")

        assert len(mentions) == 0

    def test_none_content(self, notification_manager):
        """Test parsing None content."""
        mentions = notification_manager.parse_mentions(None)

        assert len(mentions) == 0

    def test_whitespace_only_content(self, notification_manager):
        """Test parsing whitespace-only content."""
        mentions = notification_manager.parse_mentions("   \n\t  ")

        assert len(mentions) == 0


class TestLargeContent:
    """Tests for large content handling."""

    def test_many_mentions(self, notification_manager):
        """Test content with many mentions."""
        content = " ".join(["<@123>" for _ in range(50)])
        mentions = notification_manager.parse_mentions(content)

        assert len(mentions) == 50

    def test_long_content_preview_truncation(
        self, auth_manager, messaging_manager, notification_manager
    ):
        """Test long content is truncated in preview."""
        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user1 = auth_manager.register(
                username="testuser3", email="user3@example.com", password="TestPass123!"
            )
            user2 = auth_manager.register(
                username="testuser4", email="user4@example.com", password="TestPass123!"
            )

        dm = messaging_manager.create_dm(user1.id, user2.id)

        long_text = "A" * 200
        content = f"<@{user2.id}> {long_text}"
        msg = messaging_manager.send_message(user1.id, dm.id, content)

        notifs = notification_manager.create_notifications_for_message(
            author_id=user1.id,
            message_id=msg.id,
            conversation_id=dm.id,
            content=content,
        )

        assert len(notifs) == 1
        assert len(notifs[0].content_preview) <= 103
        assert notifs[0].content_preview.endswith("...")


class TestMixedMentions:
    """Tests for mixed mention types."""

    def test_all_mention_types(self, notification_manager):
        """Test content with all mention types."""
        content = "<@123> <@&456> <#789> @everyone @here"
        mentions = notification_manager.parse_mentions(content)

        types = {m.mention_type for m in mentions}
        assert MentionType.USER in types
        assert MentionType.ROLE in types
        assert MentionType.CHANNEL in types
        assert MentionType.EVERYONE in types
        assert MentionType.HERE in types

    def test_duplicate_mentions_parsed(self, notification_manager):
        """Test duplicate mentions are all parsed."""
        content = "<@123> <@123> <@123>"
        mentions = notification_manager.parse_mentions(content)

        assert len(mentions) == 3

    def test_adjacent_mentions(self, notification_manager):
        """Test adjacent mentions are parsed correctly."""
        content = "<@111><@222><@333>"
        mentions = notification_manager.parse_mentions(content)

        assert len(mentions) == 3
        assert mentions[0].target_id == 111
        assert mentions[1].target_id == 222
        assert mentions[2].target_id == 333


class TestSpecialCharacters:
    """Tests for special characters in content."""

    def test_mentions_with_special_chars(self, notification_manager):
        """Test mentions surrounded by special characters."""
        content = "!<@123>? (<@456>) [<@789>]"
        mentions = notification_manager.parse_mentions(content)

        assert len(mentions) == 3

    def test_mentions_in_code_blocks(self, notification_manager):
        """Test mentions in code blocks are still parsed."""
        content = "```<@123>```"
        mentions = notification_manager.parse_mentions(content)

        assert len(mentions) == 1

    def test_mentions_with_newlines(self, notification_manager):
        """Test mentions with newlines."""
        content = "<@111>\n<@222>\n<@333>"
        mentions = notification_manager.parse_mentions(content)

        assert len(mentions) == 3


class TestNotificationGet:
    """Tests for getting notifications."""

    def test_get_nonexistent_notification(self, notification_manager):
        """Test getting nonexistent notification returns None."""
        notif = notification_manager.get_notification(999999999)

        assert notif is None

    def test_get_existing_notification(
        self, auth_manager, messaging_manager, notification_manager
    ):
        """Test getting existing notification."""
        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user1 = auth_manager.register(
                username="testuser5", email="user5@example.com", password="TestPass123!"
            )
            user2 = auth_manager.register(
                username="testuser6", email="user6@example.com", password="TestPass123!"
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

        notif = notification_manager.get_notification(notifs[0].id)

        assert notif is not None
        assert notif.id == notifs[0].id
        assert notif.user_id == user2.id
