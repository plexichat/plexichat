"""Tests for automod audit logging."""

import pytest

from src.core.automod.models import RuleType, ActionType, ViolationSeverity, RuleMatch


@pytest.mark.automod
class TestAudit:
    """Tests for automod audit log entries."""

    def test_get_audit_log_empty(self, automod_manager, test_server):
        """Test getting audit log when empty."""
        server, owner = test_server
        entries = automod_manager.get_audit_log(server.id)
        assert isinstance(entries, list)

    def test_audit_log_after_manual_action(self, automod_manager, test_server):
        """Test audit log entry created after manual action."""
        server, owner = test_server
        automod_manager.trigger_action(
            user_id=owner.id,
            server_id=server.id,
            target_user_id=99999,
            action_type=ActionType.LOG_ONLY,
            reason="Manual test action",
        )
        entries = automod_manager.get_audit_log(server.id)
        assert len(entries) >= 1
        assert entries[0].action_type == ActionType.LOG_ONLY
        assert entries[0].moderator_id == owner.id

    def test_audit_log_with_action_type_filter(self, automod_manager, test_server):
        """Test filtering audit log by action type."""
        server, owner = test_server
        automod_manager.trigger_action(
            user_id=owner.id,
            server_id=server.id,
            target_user_id=99999,
            action_type=ActionType.LOG_ONLY,
            reason="Test",
        )
        entries = automod_manager.get_audit_log(
            server.id, action_type=ActionType.LOG_ONLY
        )
        assert all(e.action_type == ActionType.LOG_ONLY for e in entries)

    def test_audit_log_pagination(self, automod_manager, test_server):
        """Test audit log pagination with limit."""
        server, owner = test_server
        for i in range(5):
            automod_manager.trigger_action(
                user_id=owner.id,
                server_id=server.id,
                target_user_id=99999 + i,
                action_type=ActionType.LOG_ONLY,
                reason=f"Test action {i}",
            )
        entries = automod_manager.get_audit_log(server.id, limit=2)
        assert len(entries) <= 2
