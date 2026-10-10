"""Comprehensive Notifications tests targeting 80%+ coverage."""

from unittest.mock import Mock
from src.core.notifications.models import MentionType, Mention


class TestNotificationErrors:
    def test_parse_mentions(self, notification_manager):
        """Parse various mention types."""
        # Only the canonical mention syntaxes are recognized: @everyone/@here
        # and <@user>/<@&role>/<#channel>. @user#discriminator style is not
        # a supported mention form and must not produce mentions.
        content = "@user#123 @role#456 @everyone <#789>"
        mentions = notification_manager.parse_mentions(content)
        assert len(mentions) == 2
        types = sorted(m.mention_type.value for m in mentions)
        assert types == sorted([MentionType.CHANNEL.value, MentionType.EVERYONE.value])
        channels = [m for m in mentions if m.mention_type == MentionType.CHANNEL]
        everyones = [m for m in mentions if m.mention_type == MentionType.EVERYONE]
        assert len(channels) == 1 and channels[0].target_id == 789
        assert len(everyones) == 1 and everyones[0].target_id is None

    def test_validate_mention_nonexistent_user(self, notification_manager):
        """Invalid user mention."""
        mentions = [Mention(MentionType.USER, 99999, "@user", 0, 5, True)]

        validated = notification_manager.validate_mentions(1, mentions)
        assert not validated[0].valid

    def test_validate_mention_nonmentionable_role(self, notification_manager):
        """Non-mentionable role."""
        mentions = [Mention(MentionType.ROLE, 1, "@role", 0, 5, True)]

        validated = notification_manager.validate_mentions(2, mentions, server_id=1)
        assert not validated[0].valid

    def test_validate_everyone_no_permission(self, notification_manager, monkeypatch):
        """Cannot use @everyone without permission."""
        mock_servers = Mock()
        mock_servers.has_permission = Mock(return_value=False)
        monkeypatch.setattr(notification_manager, "_servers", mock_servers)

        mentions = [Mention(MentionType.EVERYONE, None, "@everyone", 0, 9, True)]

        validated = notification_manager.validate_mentions(
            1, mentions, server_id=1, channel_id=1
        )
        assert not validated[0].valid
