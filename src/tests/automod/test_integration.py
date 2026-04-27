"""Tests for automod integration with other modules."""

import pytest

from src.core.automod.models import RuleType, ActionType, ViolationSeverity, RuleMatch


@pytest.mark.automod
class TestIntegration:
    """Tests for automod integration with servers, messaging, and notifications."""

    def test_check_message_triggers_rule(self, automod_manager, test_server):
        """Test that check_message detects content matching a rule."""
        server, owner = test_server
        rule = automod_manager.create_rule(
            user_id=owner.id,
            server_id=server.id,
            name="Profanity Filter",
            rule_type=RuleType.KEYWORD,
            rule_config={
                "keywords": ["badword"],
                "case_sensitive": False,
                "whole_word": True,
            },
            actions=[{"action_type": "log_only"}],
        )

        channel = automod_manager._db.fetch_one(
            "SELECT id FROM srv_channels WHERE server_id = ? LIMIT 1", (server.id,)
        )
        channel_id = channel["id"] if channel else 0

        result = automod_manager.check_message(
            server_id=server.id,
            channel_id=channel_id,
            user_id=owner.id,
            content="This contains badword in it",
        )
        assert result is not None
        assert len(result) >= 1
        assert result[0].rule_id == rule.id

    def test_check_message_no_match(self, automod_manager, test_server):
        """Test that check_message returns empty when no rules match."""
        server, owner = test_server
        automod_manager.create_rule(
            user_id=owner.id,
            server_id=server.id,
            name="Filter",
            rule_type=RuleType.KEYWORD,
            rule_config={
                "keywords": ["badword"],
                "case_sensitive": False,
                "whole_word": True,
            },
            actions=[{"action_type": "log_only"}],
        )

        channel = automod_manager._db.fetch_one(
            "SELECT id FROM srv_channels WHERE server_id = ? LIMIT 1", (server.id,)
        )
        channel_id = channel["id"] if channel else 0

        result = automod_manager.check_message(
            server_id=server.id,
            channel_id=channel_id,
            user_id=owner.id,
            content="This is a perfectly fine message",
        )
        assert result is not None
        assert len(result) == 0

    def test_process_violation_records_in_audit_log(self, automod_manager, test_server):
        """Test that processing a violation creates an audit log entry."""
        server, owner = test_server
        rule = automod_manager.create_rule(
            user_id=owner.id,
            server_id=server.id,
            name="Audit Rule",
            rule_type=RuleType.KEYWORD,
            rule_config={
                "keywords": ["audit"],
                "case_sensitive": False,
                "whole_word": True,
            },
            actions=[{"action_type": "log_only"}],
        )

        channel = automod_manager._db.fetch_one(
            "SELECT id FROM srv_channels WHERE server_id = ? LIMIT 1", (server.id,)
        )
        channel_id = channel["id"] if channel else 0

        match = RuleMatch(
            rule_id=rule.id,
            rule_type=RuleType.KEYWORD,
            matched=True,
            matched_content="audit",
            severity=ViolationSeverity.MEDIUM,
        )
        automod_manager.process_violation(
            server_id=server.id,
            channel_id=channel_id,
            user_id=99999,
            message_id=None,
            match=match,
            actions=rule.actions,
        )

        entries = automod_manager.get_audit_log(server.id)
        assert len(entries) >= 1

    def test_disabled_rule_does_not_trigger(self, automod_manager, test_server):
        """Test that a disabled rule does not trigger on message check."""
        server, owner = test_server
        rule = automod_manager.create_rule(
            user_id=owner.id,
            server_id=server.id,
            name="Disabled Rule",
            rule_type=RuleType.KEYWORD,
            rule_config={
                "keywords": ["shouldnotmatch"],
                "case_sensitive": False,
                "whole_word": True,
            },
            actions=[{"action_type": "log_only"}],
        )

        automod_manager.set_rule_enabled(owner.id, rule.id, False)

        channel = automod_manager._db.fetch_one(
            "SELECT id FROM srv_channels WHERE server_id = ? LIMIT 1", (server.id,)
        )
        channel_id = channel["id"] if channel else 0

        result = automod_manager.check_message(
            server_id=server.id,
            channel_id=channel_id,
            user_id=owner.id,
            content="This contains shouldnotmatch word",
        )
        assert len(result) == 0

    def test_exempt_channel_skips_rule(self, automod_manager, test_server):
        """Test that a channel exemption prevents rule enforcement."""
        server, owner = test_server
        automod_manager.create_rule(
            user_id=owner.id,
            server_id=server.id,
            name="Exempt Test Rule",
            rule_type=RuleType.KEYWORD,
            rule_config={
                "keywords": ["exemptword"],
                "case_sensitive": False,
                "whole_word": True,
            },
            actions=[{"action_type": "log_only"}],
        )

        channel = automod_manager._db.fetch_one(
            "SELECT id FROM srv_channels WHERE server_id = ? LIMIT 1", (server.id,)
        )
        channel_id = channel["id"] if channel else 0

        # Add channel exemption
        automod_manager.add_exemption(
            user_id=owner.id,
            server_id=server.id,
            target_type="channel",
            target_id=channel_id,
        )

        result = automod_manager.check_message(
            server_id=server.id,
            channel_id=channel_id,
            user_id=owner.id,
            content="This contains exemptword",
        )
        assert len(result) == 0

    def test_reputation_decreases_with_violations(self, automod_manager, test_server):
        """Test that reputation score decreases when violations are processed."""
        server, owner = test_server
        rule = automod_manager.create_rule(
            user_id=owner.id,
            server_id=server.id,
            name="Rep Rule",
            rule_type=RuleType.KEYWORD,
            rule_config={
                "keywords": ["repbad"],
                "case_sensitive": False,
                "whole_word": True,
            },
            actions=[{"action_type": "log_only"}],
        )

        channel = automod_manager._db.fetch_one(
            "SELECT id FROM srv_channels WHERE server_id = ? LIMIT 1", (server.id,)
        )
        channel_id = channel["id"] if channel else 0

        initial_rep = automod_manager.get_user_reputation(88888, server.id)

        match = RuleMatch(
            rule_id=rule.id,
            rule_type=RuleType.KEYWORD,
            matched=True,
            matched_content="repbad",
            severity=ViolationSeverity.MEDIUM,
        )
        automod_manager.process_violation(
            server_id=server.id,
            channel_id=channel_id,
            user_id=88888,
            message_id=None,
            match=match,
            actions=rule.actions,
        )

        after_rep = automod_manager.get_user_reputation(88888, server.id)
        assert after_rep.score < initial_rep.score
