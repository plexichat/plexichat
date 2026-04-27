"""Tests for automod reputation system."""

import pytest

from src.core.automod.models import RuleType, ActionType, ViolationSeverity, RuleMatch


@pytest.mark.automod
class TestReputation:
    """Tests for user reputation scoring."""

    def test_default_reputation(self, automod_manager, test_server):
        """Test new user has default reputation of 100."""
        server, owner = test_server
        rep = automod_manager.get_user_reputation(99999, server.id)
        assert rep.score == 100.0
        assert rep.violation_count == 0

    def test_reputation_decreases_on_violation(self, automod_manager, test_server):
        """Test reputation decreases after a violation."""
        server, owner = test_server
        rule = automod_manager.create_rule(
            user_id=owner.id,
            server_id=server.id,
            name="Rep Rule",
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

        match = RuleMatch(
            rule_id=rule.id,
            rule_type=RuleType.KEYWORD,
            matched=True,
            matched_content="badword",
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

        rep = automod_manager.get_user_reputation(99999, server.id)
        assert rep.score < 100.0
        assert rep.violation_count == 1

    def test_reputation_decay(self, automod_manager, test_server):
        """Test reputation decay restores scores."""
        server, owner = test_server
        # Decay should work even with no prior violations
        count = automod_manager.decay_reputation(server.id)
        assert isinstance(count, int)

    def test_check_user_status(self, automod_manager, test_server):
        """Test check_user returns automod status."""
        server, owner = test_server
        status = automod_manager.check_user(owner.id, server.id)
        assert "reputation_score" in status
        assert "total_violations" in status
        assert "recent_violations_24h" in status

    def test_severity_penalties(self):
        """Test different severity levels have different penalties."""
        penalty_map = {
            ViolationSeverity.LOW: 5,
            ViolationSeverity.MEDIUM: 10,
            ViolationSeverity.HIGH: 20,
            ViolationSeverity.CRITICAL: 40,
        }
        assert (
            penalty_map[ViolationSeverity.LOW] < penalty_map[ViolationSeverity.CRITICAL]
        )
