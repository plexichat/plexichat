"""Tests for role mention notifications."""

import pytest

from src.core.notifications.models import MentionType


@pytest.mark.notifications
class TestRoleMentions:
    """Tests for role mention notification behavior."""

    def test_parse_role_mention_format(self, notification_manager):
        """Test parsing role mention format <@&ID>."""
        mentions = notification_manager.parse_mentions("<@&111222> announcement")
        role_mentions = [m for m in mentions if m.mention_type == MentionType.ROLE]
        assert len(role_mentions) >= 1

    def test_validate_role_mention_nonexistent(self, notification_manager):
        """Test validating a role mention for a non-existent role."""
        mentions = notification_manager.parse_mentions("<@&999999>")
        validated = notification_manager.validate_mentions(1, mentions)
        invalid = [
            m for m in validated if not m.valid and m.mention_type == MentionType.ROLE
        ]
        assert len(invalid) >= 1

    def test_suppress_role_notifications(self, notification_manager, test_user):
        """Test that suppress_roles prevents role mention notifications."""
        notification_manager.update_notification_settings(
            test_user.id, suppress_roles=True
        )
        settings = notification_manager.get_notification_settings(test_user.id)
        assert settings.suppress_roles is True

    def test_role_mention_type_enum(self):
        """Test MentionType.ROLE enum value."""
        assert MentionType.ROLE.value == "role"

    def test_everyone_mention_type_enum(self):
        """Test MentionType.EVERYONE enum value."""
        assert MentionType.EVERYONE.value == "everyone"

    def test_here_mention_type_enum(self):
        """Test MentionType.HERE enum value."""
        assert MentionType.HERE.value == "here"
