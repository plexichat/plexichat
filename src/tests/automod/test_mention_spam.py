"""Tests for automod mention spam rules."""

import pytest

from src.core.automod.models import RuleType
from src.core.automod.exceptions import RuleValidationError


@pytest.mark.automod
class TestMentionSpam:
    """Tests for mention spam detection rules."""

    def test_create_mention_spam_rule(self, automod_manager, test_server):
        """Test creating a mention spam rule."""
        server, owner = test_server
        rule = automod_manager.create_rule(
            user_id=owner.id,
            server_id=server.id,
            name="Mention Spam Guard",
            rule_type=RuleType.MENTION_SPAM,
            rule_config={
                "max_user_mentions": 5,
                "max_role_mentions": 3,
                "max_total_mentions": 7,
            },
            actions=[{"action_type": "delete_message"}],
        )
        assert rule.name == "Mention Spam Guard"
        assert rule.rule_type == RuleType.MENTION_SPAM

    def test_mention_spam_config_validation(self, automod_manager, test_server):
        """Test mention spam rule requires proper config."""
        server, owner = test_server
        with pytest.raises(RuleValidationError):
            automod_manager.create_rule(
                user_id=owner.id,
                server_id=server.id,
                name="Invalid",
                rule_type=RuleType.MENTION_SPAM,
                rule_config={},  # Missing required fields
                actions=[{"action_type": "log_only"}],
            )

    def test_mention_spam_rule_retrieved(self, automod_manager, test_server):
        """Test mention spam rule can be retrieved after creation."""
        server, owner = test_server
        rule = automod_manager.create_rule(
            user_id=owner.id,
            server_id=server.id,
            name="Mention Rule",
            rule_type=RuleType.MENTION_SPAM,
            rule_config={
                "max_user_mentions": 3,
                "max_role_mentions": 2,
            },
            actions=[{"action_type": "delete_message"}],
        )
        retrieved = automod_manager.get_rule(rule.id)
        assert retrieved is not None
        assert retrieved.rule_type == RuleType.MENTION_SPAM
