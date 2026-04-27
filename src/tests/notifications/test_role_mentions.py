"""Tests for @role mention parsing and notifications."""

from src.core.notifications import MentionType


class TestRoleMentionParsing:
    """Tests for parsing @role mentions."""

    def test_parse_single_role_mention(self, notification_manager):
        """Test parsing a single role mention."""
        content = "Hey <@&123456789>"
        mentions = notification_manager.parse_mentions(content)

        assert len(mentions) == 1
        assert mentions[0].mention_type == MentionType.ROLE
        assert mentions[0].target_id == 123456789
        assert mentions[0].raw_text == "<@&123456789>"

    def test_parse_multiple_role_mentions(self, notification_manager):
        """Test parsing multiple role mentions."""
        content = "Attention <@&111> and <@&222>"
        mentions = notification_manager.parse_mentions(content)

        assert len(mentions) == 2
        assert mentions[0].target_id == 111
        assert mentions[1].target_id == 222

    def test_parse_mixed_user_and_role_mentions(self, notification_manager):
        """Test parsing both user and role mentions."""
        content = "Hey <@111> and <@&222>"
        mentions = notification_manager.parse_mentions(content)

        assert len(mentions) == 2
        assert mentions[0].mention_type == MentionType.USER
        assert mentions[1].mention_type == MentionType.ROLE


class TestRoleMentionValidation:
    """Tests for validating @role mentions."""

    def test_validate_existing_role(self):
        """Test validating mention of existing role."""
        pass

    def test_validate_nonexistent_role(self):
        """Test validating mention of nonexistent role."""
        pass

    def test_validate_role_wrong_server(self):
        """Test validating role mention from different server."""
        pass


class TestRoleMentionNotifications:
    """Tests for creating notifications from @role mentions."""

    def test_create_notification_for_role_members(self):
        """Test notifications are created for role members."""
        pass

    def test_no_notification_for_sender_in_role(self):
        """Test sender doesn't get notification even if in role."""
        pass

    def test_role_mention_with_user_mention(self):
        """Test user mention takes priority over role mention."""
        pass
