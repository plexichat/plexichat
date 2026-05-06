"""Tests for automod action execution."""

import pytest

from src.core.automod.models import (
    RuleType,
    ActionType,
    ViolationSeverity,
    RuleMatch,
    RuleAction,
)


@pytest.mark.automod
class TestActions:
    """Tests for automod action types and execution."""

    def test_action_types_enum(self):
        """Test all action types exist."""
        assert ActionType.DELETE_MESSAGE.value == "delete_message"
        assert ActionType.TIMEOUT_USER.value == "timeout_user"
        assert ActionType.KICK_USER.value == "kick_user"
        assert ActionType.BAN_USER.value == "ban_user"
        assert ActionType.ALERT_MODERATORS.value == "alert_moderators"
        assert ActionType.LOG_ONLY.value == "log_only"

    def test_log_only_action_in_violation(self, automod_manager, test_server):
        """Test log_only action is recorded in violations."""
        server, owner = test_server
        rule = automod_manager.create_rule(
            user_id=owner.id,
            server_id=server.id,
            name="Log Rule",
            rule_type=RuleType.KEYWORD,
            rule_config={
                "keywords": ["testbad"],
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
            matched_content="testbad",
            severity=ViolationSeverity.LOW,
        )

        violation = automod_manager.process_violation(
            server_id=server.id,
            channel_id=channel_id,
            user_id=99999,
            message_id=None,
            match=match,
            actions=rule.actions,
        )
        assert ActionType.LOG_ONLY in violation.actions_taken

    def test_rule_action_dataclass(self):
        """Test RuleAction dataclass creation."""
        action = RuleAction(
            action_type=ActionType.DELETE_MESSAGE,
            duration_seconds=None,
            reason="Test reason",
            notify_user=True,
        )
        assert action.action_type == ActionType.DELETE_MESSAGE
        assert action.reason == "Test reason"
        assert action.notify_user is True

    def test_timeout_action_has_duration(self):
        """Test timeout action stores duration."""
        action = RuleAction(
            action_type=ActionType.TIMEOUT_USER,
            duration_seconds=300,
            reason="Spam",
        )
        assert action.duration_seconds == 300

    def test_trigger_manual_action(self, automod_manager, test_server):
        """Test manually triggering an automod action."""
        server, owner = test_server
        result = automod_manager.trigger_action(
            user_id=owner.id,
            server_id=server.id,
            target_user_id=99999,
            action_type=ActionType.LOG_ONLY,
            reason="Manual test action",
        )
        assert result is True

    def test_invalid_action_type_rejected(self, automod_manager, test_server):
        """Test that invalid action type raises validation error."""
        server, owner = test_server
        from src.core.automod.exceptions import RuleValidationError

        with pytest.raises(RuleValidationError):
            automod_manager.create_rule(
                user_id=owner.id,
                server_id=server.id,
                name="Bad Action",
                rule_type=RuleType.KEYWORD,
                rule_config={
                    "keywords": ["test"],
                    "case_sensitive": False,
                    "whole_word": True,
                },
                actions=[{"action_type": "nonexistent_action"}],
            )
