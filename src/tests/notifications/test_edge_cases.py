"""
Tests for edge cases and error handling.
"""

import pytest
from src.core.notifications import (
    NotificationNotFoundError,
    MentionType,
)


class TestInvalidMentions:
    """Tests for invalid mention handling."""

    def test_malformed_user_mention(self, db_and_modules):
        """Test malformed user mention is not parsed."""
        db, auth, messaging, servers, relationships, presence, notifications = db_and_modules

        content = "<@abc> <@> <@ 123>"
        mentions = notifications.parse_mentions(content)

        assert len(mentions) == 0

    def test_malformed_role_mention(self, db_and_modules):
        """Test malformed role mention is not parsed."""
        db, auth, messaging, servers, relationships, presence, notifications = db_and_modules

        content = "<@&abc> <@&> <@& 123>"
        mentions = notifications.parse_mentions(content)

        assert len(mentions) == 0

    def test_malformed_channel_mention(self, db_and_modules):
        """Test malformed channel mention is not parsed."""
        db, auth, messaging, servers, relationships, presence, notifications = db_and_modules

        content = "<#abc> <#> <# 123>"
        mentions = notifications.parse_mentions(content)

        assert len(mentions) == 0

    def test_partial_everyone_mention(self, db_and_modules):
        """Test partial @everyone is not parsed."""
        db, auth, messaging, servers, relationships, presence, notifications = db_and_modules

        content = "@every @everyon everyone@"
        mentions = notifications.parse_mentions(content)

        assert len(mentions) == 0

    def test_partial_here_mention(self, db_and_modules):
        """Test partial @here is not parsed."""
        db, auth, messaging, servers, relationships, presence, notifications = db_and_modules

        content = "@her @her here@"
        mentions = notifications.parse_mentions(content)

        assert len(mentions) == 0


class TestNotificationErrors:
    """Tests for notification error handling."""

    def test_mark_nonexistent_notification_read(self, fresh_users):
        """Test marking nonexistent notification as read raises error."""
        user1, user2, auth, messaging, servers, relationships, presence, notifications = fresh_users

        with pytest.raises(NotificationNotFoundError):
            notifications.mark_notification_read(user1.id, 999999999)

    def test_mark_other_users_notification_read(self, users_with_dm):
        """Test cannot mark another user's notification as read."""
        user1, user2, dm, messaging, notifications = users_with_dm

        content = f"<@{user2.id}>"
        msg = messaging.send_message(user1.id, dm.id, content)

        notifs = notifications.create_notifications_for_message(
            author_id=user1.id,
            message_id=msg.id,
            conversation_id=dm.id,
            content=content
        )

        with pytest.raises(NotificationNotFoundError):
            notifications.mark_notification_read(user1.id, notifs[0].id)

    def test_delete_nonexistent_notification(self, fresh_users):
        """Test deleting nonexistent notification raises error."""
        user1, user2, auth, messaging, servers, relationships, presence, notifications = fresh_users

        with pytest.raises(NotificationNotFoundError):
            notifications.delete_notification(user1.id, 999999999)


class TestEmptyContent:
    """Tests for empty or null content handling."""

    def test_empty_string_content(self, db_and_modules):
        """Test parsing empty string content."""
        db, auth, messaging, servers, relationships, presence, notifications = db_and_modules

        mentions = notifications.parse_mentions("")

        assert len(mentions) == 0

    def test_none_content(self, db_and_modules):
        """Test parsing None content."""
        db, auth, messaging, servers, relationships, presence, notifications = db_and_modules

        mentions = notifications.parse_mentions(None)

        assert len(mentions) == 0

    def test_whitespace_only_content(self, db_and_modules):
        """Test parsing whitespace-only content."""
        db, auth, messaging, servers, relationships, presence, notifications = db_and_modules

        mentions = notifications.parse_mentions("   \n\t  ")

        assert len(mentions) == 0


class TestLargeContent:
    """Tests for large content handling."""

    def test_many_mentions(self, group_conversation):
        """Test content with many mentions."""
        owner, member1, member2, group, messaging, notifications, relationships = group_conversation

        content = " ".join([f"<@{member1.id}>" for _ in range(50)])
        mentions = notifications.parse_mentions(content)

        assert len(mentions) == 50

    def test_long_content_preview_truncation(self, users_with_dm):
        """Test long content is truncated in preview."""
        user1, user2, dm, messaging, notifications = users_with_dm

        long_text = "A" * 200
        content = f"<@{user2.id}> {long_text}"
        msg = messaging.send_message(user1.id, dm.id, content)

        notifs = notifications.create_notifications_for_message(
            author_id=user1.id,
            message_id=msg.id,
            conversation_id=dm.id,
            content=content
        )

        assert len(notifs) == 1
        assert len(notifs[0].content_preview) <= 103
        assert notifs[0].content_preview.endswith("...")


class TestMixedMentions:
    """Tests for mixed mention types."""

    def test_all_mention_types(self, users_with_role):
        """Test content with all mention types."""
        owner, member1, member2, server, channel, role, servers, messaging, notifications = users_with_role

        content = f"<@{member1.id}> <@&{role.id}> <#{channel.id}> @everyone @here"
        mentions = notifications.parse_mentions(content)

        types = {m.mention_type for m in mentions}
        assert MentionType.USER in types
        assert MentionType.ROLE in types
        assert MentionType.CHANNEL in types
        assert MentionType.EVERYONE in types
        assert MentionType.HERE in types

    def test_duplicate_mentions_parsed(self, db_and_modules):
        """Test duplicate mentions are all parsed."""
        db, auth, messaging, servers, relationships, presence, notifications = db_and_modules

        content = "<@123> <@123> <@123>"
        mentions = notifications.parse_mentions(content)

        assert len(mentions) == 3

    def test_adjacent_mentions(self, db_and_modules):
        """Test adjacent mentions are parsed correctly."""
        db, auth, messaging, servers, relationships, presence, notifications = db_and_modules

        content = "<@111><@222><@333>"
        mentions = notifications.parse_mentions(content)

        assert len(mentions) == 3
        assert mentions[0].target_id == 111
        assert mentions[1].target_id == 222
        assert mentions[2].target_id == 333


class TestSpecialCharacters:
    """Tests for special characters in content."""

    def test_mentions_with_special_chars(self, db_and_modules):
        """Test mentions surrounded by special characters."""
        db, auth, messaging, servers, relationships, presence, notifications = db_and_modules

        content = "!<@123>? (<@456>) [<@789>]"
        mentions = notifications.parse_mentions(content)

        assert len(mentions) == 3

    def test_mentions_in_code_blocks(self, db_and_modules):
        """Test mentions in code blocks are still parsed."""
        db, auth, messaging, servers, relationships, presence, notifications = db_and_modules

        content = "```<@123>```"
        mentions = notifications.parse_mentions(content)

        assert len(mentions) == 1

    def test_mentions_with_newlines(self, db_and_modules):
        """Test mentions with newlines."""
        db, auth, messaging, servers, relationships, presence, notifications = db_and_modules

        content = "<@111>\n<@222>\n<@333>"
        mentions = notifications.parse_mentions(content)

        assert len(mentions) == 3


class TestNotificationGet:
    """Tests for getting notifications."""

    def test_get_nonexistent_notification(self, fresh_users):
        """Test getting nonexistent notification returns None."""
        user1, user2, auth, messaging, servers, relationships, presence, notifications = fresh_users

        notif = notifications.get_notification(999999999)

        assert notif is None

    def test_get_existing_notification(self, users_with_dm):
        """Test getting existing notification."""
        user1, user2, dm, messaging, notifications = users_with_dm

        content = f"<@{user2.id}>"
        msg = messaging.send_message(user1.id, dm.id, content)

        notifs = notifications.create_notifications_for_message(
            author_id=user1.id,
            message_id=msg.id,
            conversation_id=dm.id,
            content=content
        )

        assert len(notifs) == 1

        notif = notifications.get_notification(notifs[0].id)

        assert notif is not None
        assert notif.id == notifs[0].id
        assert notif.user_id == user2.id
