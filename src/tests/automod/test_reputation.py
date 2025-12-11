"""
Tests for reputation system.
"""

import pytest

from src.core.automod import RuleType


@pytest.mark.automod
class TestReputationSystem:
    """Tests for user reputation system."""

    def test_initial_reputation(self, automod_module, test_server_for_automod, user_pool):
        """Test new user has default reputation."""
        server, channel, owner = test_server_for_automod
        user = user_pool.get_user()

        reputation = automod_module.get_user_reputation(user.id, server.id)

        assert reputation.score == 100.0
        assert reputation.violation_count == 0
        assert reputation.last_violation_at is None

    def test_reputation_decreases_on_violation(self, automod_module, test_server_for_automod, user_pool):
        """Test reputation decreases after violation."""
        server, channel, owner = test_server_for_automod
        user = user_pool.get_user()

        rule = automod_module.create_rule(
            user_id=owner.id,
            server_id=server.id,
            name="Rep Test",
            rule_type=RuleType.KEYWORD,
            rule_config={"keywords": ["bad"], "case_sensitive": False, "whole_word": True},
            actions=[{"action_type": "log_only"}]
        )

        result = automod_module.check_message(
            server_id=server.id,
            channel_id=channel.id,
            user_id=user.id,
            content="bad word"
        )

        if not result.passed:
            for match in result.violations:
                automod_module.process_violation(
                    server_id=server.id,
                    channel_id=channel.id,
                    user_id=user.id,
                    message_id=None,
                    match=match,
                    actions=result.actions_to_take
                )

        reputation = automod_module.get_user_reputation(user.id, server.id)

        assert reputation.score < 100.0
        assert reputation.violation_count == 1
        assert reputation.last_violation_at is not None

    def test_reputation_decay(self, automod_module, test_server_for_automod, user_pool):
        """Test reputation can be restored via decay."""
        server, channel, owner = test_server_for_automod
        user = user_pool.get_user()

        rule = automod_module.create_rule(
            user_id=owner.id,
            server_id=server.id,
            name="Rep Test",
            rule_type=RuleType.KEYWORD,
            rule_config={"keywords": ["bad"], "case_sensitive": False, "whole_word": True},
            actions=[{"action_type": "log_only"}]
        )

        result = automod_module.check_message(
            server_id=server.id,
            channel_id=channel.id,
            user_id=user.id,
            content="bad word"
        )

        if not result.passed:
            for match in result.violations:
                automod_module.process_violation(
                    server_id=server.id,
                    channel_id=channel.id,
                    user_id=user.id,
                    message_id=None,
                    match=match,
                    actions=result.actions_to_take
                )

        rep_before = automod_module.get_user_reputation(user.id, server.id)

        updated_count = automod_module.decay_reputation(server.id)

        rep_after = automod_module.get_user_reputation(user.id, server.id)

        assert rep_after.score >= rep_before.score

    def test_check_user_status(self, automod_module, test_server_for_automod, user_pool):
        """Test checking user's automod status."""
        server, channel, owner = test_server_for_automod
        user = user_pool.get_user()

        status = automod_module.check_user(user.id, server.id)

        assert status["user_id"] == user.id
        assert status["server_id"] == server.id
        assert status["reputation_score"] == 100.0
        assert status["total_violations"] == 0
        assert status["recent_violations_24h"] == 0
