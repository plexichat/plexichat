"""
Tests for spam detection rules.
"""

import pytest
import time

from src.core import automod
from src.core.automod import RuleType
from src.core.automod.rules.spam import MessageSpamRule


@pytest.mark.automod
class TestMessageSpamRule:
    """Tests for MessageSpamRule."""

    def test_rate_spam_detection(self, spam_rule):
        """Test rate-based spam detection."""
        rule, server, channel, owner = spam_rule

        now = int(time.time() * 1000)
        recent_messages = [
            {"user_id": owner.id, "created_at": now - 1000, "content": "msg1"},
            {"user_id": owner.id, "created_at": now - 2000, "content": "msg2"},
            {"user_id": owner.id, "created_at": now - 3000, "content": "msg3"},
        ]

        result = automod.check_message(
            server_id=server.id,
            channel_id=channel.id,
            user_id=owner.id,
            content="Another message",
            context={"recent_messages": recent_messages}
        )

        assert not result.passed
        assert result.violations[0].rule_type == RuleType.MESSAGE_SPAM

    def test_duplicate_spam_detection(self, spam_rule):
        """Test duplicate content spam detection."""
        rule, server, channel, owner = spam_rule

        now = int(time.time() * 1000)
        duplicate_content = "This is duplicate content"
        recent_messages = [
            {"user_id": owner.id, "created_at": now - 5000, "content": duplicate_content},
            {"user_id": owner.id, "created_at": now - 10000, "content": duplicate_content},
        ]

        result = automod.check_message(
            server_id=server.id,
            channel_id=channel.id,
            user_id=owner.id,
            content=duplicate_content,
            context={"recent_messages": recent_messages}
        )

        assert not result.passed

    def test_no_spam_normal_rate(self, spam_rule):
        """Test normal message rate passes."""
        rule, server, channel, owner = spam_rule

        now = int(time.time() * 1000)
        recent_messages = [
            {"user_id": owner.id, "created_at": now - 10000, "content": "msg1"},
        ]

        result = automod.check_message(
            server_id=server.id,
            channel_id=channel.id,
            user_id=owner.id,
            content="Normal message",
            context={"recent_messages": recent_messages}
        )

        assert result.passed

    def test_other_user_messages_ignored(self, spam_rule, user_pool):
        """Test that other users' messages don't count."""
        rule, server, channel, owner = spam_rule
        other_user = user_pool.get_user()

        now = int(time.time() * 1000)
        recent_messages = [
            {"user_id": other_user.id, "created_at": now - 1000, "content": "msg1"},
            {"user_id": other_user.id, "created_at": now - 2000, "content": "msg2"},
            {"user_id": other_user.id, "created_at": now - 3000, "content": "msg3"},
        ]

        result = automod.check_message(
            server_id=server.id,
            channel_id=channel.id,
            user_id=owner.id,
            content="My message",
            context={"recent_messages": recent_messages}
        )

        assert result.passed


@pytest.mark.automod
class TestMessageSpamRuleValidation:
    """Tests for spam rule config validation."""

    def test_valid_config(self):
        """Test valid configuration passes."""
        valid, issues = MessageSpamRule.validate_config({
            "max_messages": 5,
            "window_seconds": 10,
            "duplicate_threshold": 3
        })

        assert valid

    def test_invalid_max_messages(self):
        """Test invalid max_messages fails."""
        valid, issues = MessageSpamRule.validate_config({
            "max_messages": 0
        })

        assert not valid

    def test_invalid_similarity_threshold(self):
        """Test invalid similarity threshold fails."""
        valid, issues = MessageSpamRule.validate_config({
            "similarity_threshold": 1.5
        })

        assert not valid
