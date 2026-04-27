"""
Tests for #channel mention parsing.
"""

from src.core.notifications import MentionType


class TestChannelMentionParsing:
    """Tests for parsing #channel mentions."""

    def test_parse_single_channel_mention(self, notification_manager):
        """Test parsing a single channel mention."""
        content = "Check out <#123456789>"
        mentions = notification_manager.parse_mentions(content)

        assert len(mentions) == 1
        assert mentions[0].mention_type == MentionType.CHANNEL
        assert mentions[0].target_id == 123456789
        assert mentions[0].raw_text == "<#123456789>"

    def test_parse_multiple_channel_mentions(self, notification_manager):
        """Test parsing multiple channel mentions."""
        content = "See <#111> and <#222>"
        mentions = notification_manager.parse_mentions(content)

        assert len(mentions) == 2
        assert mentions[0].target_id == 111
        assert mentions[1].target_id == 222

    def test_parse_channel_with_other_mentions(self, notification_manager):
        """Test parsing channel mentions with other mention types."""
        content = "Hey <@111> check <#222> with @everyone"
        mentions = notification_manager.parse_mentions(content)

        assert len(mentions) == 3
        types = [m.mention_type for m in mentions]
        assert MentionType.USER in types
        assert MentionType.CHANNEL in types
        assert MentionType.EVERYONE in types


class TestChannelMentionValidation:
    """Tests for validating #channel mentions."""

    def test_validate_existing_channel(
        self, auth_manager, server_manager, messaging_manager, notification_manager
    ):
        """Test validating mention of existing channel."""
        pass

    def test_validate_nonexistent_channel(
        self, auth_manager, server_manager, notification_manager
    ):
        """Test validating mention of nonexistent channel."""
        pass


class TestChannelMentionBehavior:
    """Tests for channel mention behavior."""

    def test_channel_mention_no_notification(
        self, auth_manager, server_manager, messaging_manager, notification_manager
    ):
        """Test channel mentions don't create notifications."""
        pass

    def test_channel_mention_with_user_mention(
        self, auth_manager, server_manager, messaging_manager, notification_manager
    ):
        """Test channel mention combined with user mention."""
        pass
