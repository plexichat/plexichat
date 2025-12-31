"""
Tests for #channel mention parsing.
"""

from src.core.notifications import MentionType


class TestChannelMentionParsing:
    """Tests for parsing #channel mentions."""

    def test_parse_single_channel_mention(self, db_and_modules):
        """Test parsing a single channel mention."""
        db, auth, messaging, servers, relationships, presence, notifications = db_and_modules

        content = "Check out <#123456789>"
        mentions = notifications.parse_mentions(content)

        assert len(mentions) == 1
        assert mentions[0].mention_type == MentionType.CHANNEL
        assert mentions[0].target_id == 123456789
        assert mentions[0].raw_text == "<#123456789>"

    def test_parse_multiple_channel_mentions(self, db_and_modules):
        """Test parsing multiple channel mentions."""
        db, auth, messaging, servers, relationships, presence, notifications = db_and_modules

        content = "See <#111> and <#222>"
        mentions = notifications.parse_mentions(content)

        assert len(mentions) == 2
        assert mentions[0].target_id == 111
        assert mentions[1].target_id == 222

    def test_parse_channel_with_other_mentions(self, db_and_modules):
        """Test parsing channel mentions with other mention types."""
        db, auth, messaging, servers, relationships, presence, notifications = db_and_modules

        content = "Hey <@111> check <#222> with @everyone"
        mentions = notifications.parse_mentions(content)

        assert len(mentions) == 3
        types = [m.mention_type for m in mentions]
        assert MentionType.USER in types
        assert MentionType.CHANNEL in types
        assert MentionType.EVERYONE in types


class TestChannelMentionValidation:
    """Tests for validating #channel mentions."""

    def test_validate_existing_channel(self, users_with_server):
        """Test validating mention of existing channel."""
        owner, member1, member2, server, channel, servers, messaging, notifications = users_with_server

        content = f"Check <#{channel.id}>"
        mentions = notifications.parse_mentions(content)
        validated = notifications.validate_mentions(owner.id, mentions, server.id)

        assert len(validated) == 1
        assert validated[0].valid is True

    def test_validate_nonexistent_channel(self, users_with_server):
        """Test validating mention of nonexistent channel."""
        owner, member1, member2, server, channel, servers, messaging, notifications = users_with_server

        content = "<#999999999999>"
        mentions = notifications.parse_mentions(content)
        validated = notifications.validate_mentions(owner.id, mentions, server.id)

        assert len(validated) == 1
        assert validated[0].valid is False
        assert "not found" in validated[0].error.lower()


class TestChannelMentionBehavior:
    """Tests for channel mention behavior."""

    def test_channel_mention_no_notification(self, users_with_server):
        """Test channel mentions don't create notifications."""
        owner, member1, member2, server, channel, servers, messaging, notifications = users_with_server

        group = messaging.create_group(owner.id, "Server Group", [member1.id, member2.id])

        content = f"Check out <#{channel.id}>"
        msg = messaging.send_message(owner.id, group.id, content)

        notifs = notifications.create_notifications_for_message(
            author_id=owner.id,
            message_id=msg.id,
            conversation_id=group.id,
            content=content,
            server_id=server.id
        )

        assert len(notifs) == 0

    def test_channel_mention_with_user_mention(self, users_with_server):
        """Test channel mention combined with user mention."""
        owner, member1, member2, server, channel, servers, messaging, notifications = users_with_server

        group = messaging.create_group(owner.id, "Server Group", [member1.id, member2.id])

        content = f"<@{member1.id}> check <#{channel.id}>"
        msg = messaging.send_message(owner.id, group.id, content)

        notifs = notifications.create_notifications_for_message(
            author_id=owner.id,
            message_id=msg.id,
            conversation_id=group.id,
            content=content,
            server_id=server.id
        )

        assert len(notifs) == 1
        assert notifs[0].user_id == member1.id
        assert notifs[0].mention_type == MentionType.USER
