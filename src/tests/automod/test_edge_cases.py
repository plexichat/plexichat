"""
Edge case tests for automod module.
"""

import pytest

from src.core.automod import RuleType, RuleValidationError, RuleNotFoundError


@pytest.mark.automod
class TestEdgeCases:
    """Tests for edge cases and error conditions."""

    def test_empty_content(self, automod_module, test_server_for_automod):
        """Test checking empty content."""
        server, channel, owner = test_server_for_automod

        rule = automod_module.create_rule(
            user_id=owner.id,
            server_id=server.id,
            name="Test",
            rule_type=RuleType.KEYWORD,
            rule_config={"keywords": ["test"], "case_sensitive": False, "whole_word": True},
            actions=[{"action_type": "log_only"}]
        )

        result = automod_module.check_message(
            server_id=server.id,
            channel_id=channel.id,
            user_id=owner.id,
            content=""
        )

        assert result.passed

    def test_very_long_content(self, automod_module, test_server_for_automod):
        """Test checking very long content."""
        server, channel, owner = test_server_for_automod

        rule = automod_module.create_rule(
            user_id=owner.id,
            server_id=server.id,
            name="Test",
            rule_type=RuleType.KEYWORD,
            rule_config={"keywords": ["bad"], "case_sensitive": False, "whole_word": True},
            actions=[{"action_type": "log_only"}]
        )

        long_content = "a" * 10000 + " bad " + "b" * 10000

        result = automod_module.check_message(
            server_id=server.id,
            channel_id=channel.id,
            user_id=owner.id,
            content=long_content
        )

        assert not result.passed

    def test_invalid_rule_config(self, automod_module, test_server_for_automod):
        """Test creating rule with invalid config."""
        server, channel, owner = test_server_for_automod

        with pytest.raises(RuleValidationError):
            automod_module.create_rule(
                user_id=owner.id,
                server_id=server.id,
                name="Invalid",
                rule_type=RuleType.KEYWORD,
                rule_config={"keywords": "not a list"},
                actions=[{"action_type": "log_only"}]
            )

    def test_invalid_action_type(self, automod_module, test_server_for_automod):
        """Test creating rule with invalid action type."""
        server, channel, owner = test_server_for_automod

        with pytest.raises(RuleValidationError):
            automod_module.create_rule(
                user_id=owner.id,
                server_id=server.id,
                name="Invalid Action",
                rule_type=RuleType.KEYWORD,
                rule_config={"keywords": ["test"], "case_sensitive": False, "whole_word": True},
                actions=[{"action_type": "invalid_action"}]
            )

    def test_update_nonexistent_rule(self, automod_module, test_server_for_automod):
        """Test updating a rule that doesn't exist."""
        server, channel, owner = test_server_for_automod

        with pytest.raises(RuleNotFoundError):
            automod_module.update_rule(
                user_id=owner.id,
                rule_id=999999,
                name="Updated"
            )

    def test_delete_nonexistent_rule(self, automod_module, test_server_for_automod):
        """Test deleting a rule that doesn't exist."""
        server, channel, owner = test_server_for_automod

        with pytest.raises(RuleNotFoundError):
            automod_module.delete_rule(owner.id, 999999)

    def test_unicode_content(self, automod_module, test_server_for_automod):
        """Test handling unicode content."""
        server, channel, owner = test_server_for_automod

        rule = automod_module.create_rule(
            user_id=owner.id,
            server_id=server.id,
            name="Unicode Test",
            rule_type=RuleType.KEYWORD,
            rule_config={"keywords": ["test"], "case_sensitive": False, "whole_word": True},
            actions=[{"action_type": "log_only"}]
        )

        result = automod_module.check_message(
            server_id=server.id,
            channel_id=channel.id,
            user_id=owner.id,
            content="Hello 世界 test 🎉"
        )

        assert not result.passed

    def test_special_regex_characters(self, automod_module, test_server_for_automod):
        """Test keywords with special regex characters."""
        server, channel, owner = test_server_for_automod

        rule = automod_module.create_rule(
            user_id=owner.id,
            server_id=server.id,
            name="Special Chars",
            rule_type=RuleType.KEYWORD,
            rule_config={"keywords": ["$$$", "(test)"], "case_sensitive": False, "whole_word": False},
            actions=[{"action_type": "log_only"}]
        )

        result = automod_module.check_message(
            server_id=server.id,
            channel_id=channel.id,
            user_id=owner.id,
            content="Get $$$ now!"
        )

        assert not result.passed

    def test_no_rules_configured(self, automod_module, test_server_for_automod):
        """Test server with no rules configured."""
        server, channel, owner = test_server_for_automod

        result = automod_module.check_message(
            server_id=server.id,
            channel_id=channel.id,
            user_id=owner.id,
            content="any content"
        )

        assert result.passed

    def test_get_violations_empty(self, automod_module, test_server_for_automod):
        """Test getting violations when none exist."""
        server, channel, owner = test_server_for_automod

        violations = automod_module.get_violations(server.id)

        assert isinstance(violations, list)
