"""
Tests for audit logging.
"""

import pytest

from src.core.automod import RuleType, ActionType


@pytest.mark.automod
class TestAuditLog:
    """Tests for audit log functionality."""

    def test_audit_log_created_on_violation(self, automod_module, test_server_for_automod, user_pool):
        """Test audit log entry is created for violations."""
        server, channel, owner = test_server_for_automod
        user = user_pool.get_user()

        rule = automod_module.create_rule(
            user_id=owner.id,
            server_id=server.id,
            name="Audit Test",
            rule_type=RuleType.KEYWORD,
            rule_config={"keywords": ["audit"], "case_sensitive": False, "whole_word": True},
            actions=[{"action_type": "alert_moderators"}]
        )

        result = automod_module.check_message(
            server_id=server.id,
            channel_id=channel.id,
            user_id=user.id,
            content="audit test"
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

        audit_entries = automod_module.get_audit_log(server.id, limit=10)

        assert len(audit_entries) > 0
        assert audit_entries[0].server_id == server.id
        assert audit_entries[0].target_user_id == user.id

    def test_get_audit_log_with_filter(self, automod_module, test_server_for_automod):
        """Test filtering audit log by action type."""
        server, channel, owner = test_server_for_automod

        entries = automod_module.get_audit_log(
            server.id,
            action_type=ActionType.ALERT_MODERATORS
        )

        for entry in entries:
            assert entry.action_type == ActionType.ALERT_MODERATORS

    def test_manual_action_logged(self, automod_module, test_server_for_automod, user_pool):
        """Test manually triggered actions are logged."""
        server, channel, owner = test_server_for_automod
        user = user_pool.get_user()

        success = automod_module.trigger_action(
            user_id=owner.id,
            server_id=server.id,
            target_user_id=user.id,
            action_type=ActionType.LOG_ONLY,
            reason="Manual test action"
        )

        assert success

        audit_entries = automod_module.get_audit_log(server.id, limit=10)

        manual_entries = [e for e in audit_entries if e.moderator_id == owner.id]
        assert len(manual_entries) > 0
        assert manual_entries[0].reason == "Manual test action"
