"""Tests for automod spam detection."""

import pytest

from src.core.automod.models import RuleType


@pytest.mark.automod
class TestSpamDetection:
    """Tests for message spam detection rules."""

    def test_create_spam_rule(self, automod_manager, test_server):
        """Test creating a spam detection rule."""
        server, owner = test_server
        rule = automod_manager.create_rule(
            user_id=owner.id,
            server_id=server.id,
            name="Anti-Spam",
            rule_type=RuleType.MESSAGE_SPAM,
            rule_config={
                "max_messages": 5,
                "window_seconds": 10,
                "duplicate_threshold": 3,
            },
            actions=[{"action_type": "timeout_user", "duration_seconds": 60}],
        )
        assert rule.name == "Anti-Spam"
        assert rule.rule_type == RuleType.MESSAGE_SPAM

    def test_spam_rule_config_validation(self, automod_manager, test_server):
        """Test spam rule config requires proper fields."""
        server, owner = test_server
        from src.core.automod.exceptions import RuleValidationError

        with pytest.raises(RuleValidationError):
            automod_manager.create_rule(
                user_id=owner.id,
                server_id=server.id,
                name="Invalid Spam",
                rule_type=RuleType.MESSAGE_SPAM,
                rule_config={},  # Missing required fields
                actions=[{"action_type": "log_only"}],
            )

    def test_spam_rule_stores_in_db(self, automod_manager, test_server):
        """Test spam rule is persisted in database."""
        server, owner = test_server
        rule = automod_manager.create_rule(
            user_id=owner.id,
            server_id=server.id,
            name="Spam Rule",
            rule_type=RuleType.MESSAGE_SPAM,
            rule_config={
                "max_messages": 3,
                "window_seconds": 5,
            },
            actions=[{"action_type": "log_only"}],
        )

        retrieved = automod_manager.get_rule(rule.id)
        assert retrieved is not None
        assert retrieved.name == "Spam Rule"
        assert retrieved.rule_type == RuleType.MESSAGE_SPAM
