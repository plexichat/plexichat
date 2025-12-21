"""
Tests for mention spam rules.
"""

import pytest

from src.core import automod
from src.core.automod import RuleType
from src.core.automod.rules.mentions import MentionSpamRule


@pytest.mark.automod
class TestMentionSpamRule:
    """Tests for MentionSpamRule."""

    def test_user_mention_spam(self, mention_rule):
        """Test excessive user mentions are detected."""
        rule, server, channel, owner = mention_rule

        result = automod.check_message(
            server_id=server.id,
            channel_id=channel.id,
            user_id=owner.id,
            content="Hey <@123> <@456> <@789> <@101> check this out"
        )

        assert not result.passed
        assert result.violations[0].rule_type == RuleType.MENTION_SPAM

    def test_role_mention_spam(self, mention_rule):
        """Test excessive role mentions are detected."""
        rule, server, channel, owner = mention_rule

        result = automod.check_message(
            server_id=server.id,
            channel_id=channel.id,
            user_id=owner.id,
            content="Attention <@&111> <@&222> <@&333>"
        )

        assert not result.passed

    def test_everyone_blocked(self, mention_rule):
        """Test @everyone is blocked when configured."""
        rule, server, channel, owner = mention_rule

        result = automod.check_message(
            server_id=server.id,
            channel_id=channel.id,
            user_id=owner.id,
            content="Hey @everyone check this out"
        )

        assert not result.passed

    def test_here_blocked(self, mention_rule):
        """Test @here is blocked when configured."""
        rule, server, channel, owner = mention_rule

        result = automod.check_message(
            server_id=server.id,
            channel_id=channel.id,
            user_id=owner.id,
            content="Hey @here important announcement"
        )

        assert not result.passed

    def test_normal_mentions_pass(self, mention_rule):
        """Test normal mention count passes."""
        rule, server, channel, owner = mention_rule

        result = automod.check_message(
            server_id=server.id,
            channel_id=channel.id,
            user_id=owner.id,
            content="Hey <@123> <@456> check this"
        )

        assert result.passed

    def test_no_mentions_pass(self, mention_rule):
        """Test message without mentions passes."""
        rule, server, channel, owner = mention_rule

        result = automod.check_message(
            server_id=server.id,
            channel_id=channel.id,
            user_id=owner.id,
            content="Just a normal message"
        )

        assert result.passed


@pytest.mark.automod
class TestMentionSpamRuleValidation:
    """Tests for mention spam rule config validation."""

    def test_valid_config(self):
        """Test valid configuration passes."""
        valid, issues = MentionSpamRule.validate_config({
            "max_user_mentions": 5,
            "max_role_mentions": 3,
            "block_everyone": True
        })

        assert valid

    def test_invalid_mention_count(self):
        """Test negative mention count fails."""
        valid, issues = MentionSpamRule.validate_config({
            "max_user_mentions": -1
        })

        assert not valid

    def test_invalid_block_everyone_type(self):
        """Test non-boolean block_everyone fails."""
        valid, issues = MentionSpamRule.validate_config({
            "block_everyone": "yes"
        })

        assert not valid
