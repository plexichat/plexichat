"""Tests for automod regex rules."""

import pytest

from src.core.automod.models import RuleType, ActionType
from src.core.automod.exceptions import RuleValidationError


@pytest.mark.automod
class TestRegexRules:
    """Tests for regex-based automod rules."""

    def test_create_regex_rule(self, automod_manager, test_server):
        """Test creating a regex rule."""
        server, owner = test_server
        rule = automod_manager.create_rule(
            user_id=owner.id,
            server_id=server.id,
            name="Credit Card Filter",
            rule_type=RuleType.REGEX,
            rule_config={
                "patterns": [
                    {
                        "pattern": r"\b\d{16}\b",
                        "name": "credit_card",
                        "severity": "high",
                    }
                ]
            },
            actions=[
                {"action_type": "delete_message"},
                {"action_type": "alert_moderators"},
            ],
        )
        assert rule.name == "Credit Card Filter"
        assert rule.rule_type == RuleType.REGEX
        assert len(rule.actions) == 2

    def test_regex_rule_matches_pattern(self, automod_manager, test_server):
        """Test that regex rule detects matching pattern."""
        server, owner = test_server
        automod_manager.create_rule(
            user_id=owner.id,
            server_id=server.id,
            name="Link Detector",
            rule_type=RuleType.REGEX,
            rule_config={
                "patterns": [
                    {"pattern": r"https?://\S+", "name": "url", "severity": "low"}
                ]
            },
            actions=[{"action_type": "log_only"}],
        )

        channel = automod_manager._db.fetch_one(
            "SELECT id FROM srv_channels WHERE server_id = ? LIMIT 1",
            (server.id,),
        )
        channel_id = channel["id"] if channel else 0

        result = automod_manager.check_message(
            server_id=server.id,
            channel_id=channel_id,
            user_id=99999,
            content="Visit https://example.com for free stuff",
        )
        assert not result.passed

    def test_regex_rule_no_match(self, automod_manager, test_server):
        """Test that regex rule does not match non-matching content."""
        server, owner = test_server
        automod_manager.create_rule(
            user_id=owner.id,
            server_id=server.id,
            name="Link Detector",
            rule_type=RuleType.REGEX,
            rule_config={
                "patterns": [
                    {"pattern": r"https?://\S+", "name": "url", "severity": "low"}
                ]
            },
            actions=[{"action_type": "log_only"}],
        )

        channel = automod_manager._db.fetch_one(
            "SELECT id FROM srv_channels WHERE server_id = ? LIMIT 1",
            (server.id,),
        )
        channel_id = channel["id"] if channel else 0

        result = automod_manager.check_message(
            server_id=server.id,
            channel_id=channel_id,
            user_id=99999,
            content="Just a normal message with no links",
        )
        assert result.passed

    def test_regex_rule_invalid_config(self, automod_manager, test_server):
        """Test that invalid regex config raises validation error."""
        server, owner = test_server
        with pytest.raises(RuleValidationError):
            automod_manager.create_rule(
                user_id=owner.id,
                server_id=server.id,
                name="Invalid",
                rule_type=RuleType.REGEX,
                rule_config={},  # Missing required 'patterns'
                actions=[{"action_type": "log_only"}],
            )

    def test_regex_rule_multiple_patterns(self, automod_manager, test_server):
        """Test regex rule with multiple patterns."""
        server, owner = test_server
        rule = automod_manager.create_rule(
            user_id=owner.id,
            server_id=server.id,
            name="Multi Pattern",
            rule_type=RuleType.REGEX,
            rule_config={
                "patterns": [
                    {"pattern": r"free\s+money", "name": "scam", "severity": "high"},
                    {"pattern": r"\b\d{16}\b", "name": "cc", "severity": "critical"},
                ]
            },
            actions=[{"action_type": "delete_message"}],
        )
        assert rule.rule_type == RuleType.REGEX
