"""Regression tests: automod listing ``limit`` must be clamped from below.

``get_violations`` and ``get_audit_log`` capped ``limit`` from above
(``min(limit, 100)``) but never from below. A negative ``limit`` reached the
SQL ``LIMIT ?`` verbatim, and SQLite treats ``LIMIT -1`` as *unlimited* —
so ``limit=-1`` dumped the whole violation / audit-log table instead of an
empty page.
"""

import pytest

from src.core.automod.models import ActionType, RuleType, ViolationSeverity, RuleMatch


@pytest.mark.automod
class TestAutomodNegativeLimitClamp:
    def test_get_violations_negative_limit_returns_empty(
        self, automod_manager, test_server
    ):
        """A negative violation-list limit must not bypass the page cap."""
        server, owner = test_server
        rule = automod_manager.create_rule(
            user_id=owner.id,
            server_id=server.id,
            name="Clamp Rule",
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
        automod_manager.process_violation(
            server_id=server.id,
            channel_id=channel_id,
            user_id=99999,
            message_id=None,
            match=RuleMatch(
                rule_id=rule.id,
                rule_type=RuleType.KEYWORD,
                matched=True,
                matched_content="badword",
                severity=ViolationSeverity.MEDIUM,
            ),
            actions=rule.actions,
        )
        assert len(automod_manager.get_violations(server.id, limit=10)) >= 1
        assert automod_manager.get_violations(server.id, limit=-1) == []
        # A clamped negative limit behaves exactly like limit=0.
        assert automod_manager.get_violations(server.id, limit=0) == []

    def test_get_audit_log_negative_limit_returns_empty(
        self, automod_manager, test_server
    ):
        """A negative automod audit-log limit must not dump the whole log."""
        server, owner = test_server
        automod_manager.trigger_action(
            user_id=owner.id,
            server_id=server.id,
            target_user_id=99999,
            action_type=ActionType.LOG_ONLY,
            reason="Clamp test action",
        )
        assert len(automod_manager.get_audit_log(server.id, limit=10)) >= 1
        assert automod_manager.get_audit_log(server.id, limit=-1) == []
        assert automod_manager.get_audit_log(server.id, limit=0) == []
