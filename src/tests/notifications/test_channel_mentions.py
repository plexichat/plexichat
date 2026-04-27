"""Tests for notification channel and role mentions."""

import pytest

from src.core.notifications.models import MentionType, NotificationLevel


@pytest.mark.notifications
class TestChannelMentions:
    """Tests for channel and role mention notifications."""

    def test_parse_user_mention(self, notification_manager):
        """Test parsing a user mention from content."""
        mentions = notification_manager.parse_mentions("Hello <@123456789>!")
        assert len(mentions) >= 1
        user_mentions = [m for m in mentions if m.mention_type == MentionType.USER]
        assert len(user_mentions) >= 1

    def test_parse_channel_mention(self, notification_manager):
        """Test parsing a channel mention from content."""
        mentions = notification_manager.parse_mentions("See <#123456789>")
        channel_mentions = [
            m for m in mentions if m.mention_type == MentionType.CHANNEL
        ]
        assert len(channel_mentions) >= 1

    def test_parse_role_mention(self, notification_manager):
        """Test parsing a role mention from content."""
        mentions = notification_manager.parse_mentions("<@&123456789> meeting!")
        role_mentions = [m for m in mentions if m.mention_type == MentionType.ROLE]
        assert len(role_mentions) >= 1

    def test_parse_everyone_mention(self, notification_manager):
        """Test parsing @everyone mention."""
        mentions = notification_manager.parse_mentions("@everyone important!")
        everyone = [m for m in mentions if m.mention_type == MentionType.EVERYONE]
        assert len(everyone) >= 1

    def test_parse_here_mention(self, notification_manager):
        """Test parsing @here mention."""
        mentions = notification_manager.parse_mentions("@here check this!")
        here = [m for m in mentions if m.mention_type == MentionType.HERE]
        assert len(here) >= 1

    def test_parse_no_mentions(self, notification_manager):
        """Test parsing content with no mentions."""
        mentions = notification_manager.parse_mentions("Just a regular message")
        assert len(mentions) == 0

    def test_parse_multiple_mentions(self, notification_manager):
        """Test parsing content with multiple mentions."""
        mentions = notification_manager.parse_mentions("<@111> and <@222> check <#333>")
        assert len(mentions) >= 3

    def test_validate_mentions_invalid_user(self, notification_manager):
        """Test validating mentions with invalid user ID."""
        mentions = notification_manager.parse_mentions("<@999999999>")
        validated = notification_manager.validate_mentions(1, mentions)
        # Invalid user IDs should be marked as invalid
        invalid = [m for m in validated if not m.valid]
        assert len(invalid) >= 1
