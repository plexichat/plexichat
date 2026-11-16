"""Automod rule/action values must match the engine allowlists."""

import pytest

from src.core.automod.models import (
    ActionType,
    RuleType,
    ViolationSeverity,
    coerce_action_type,
    coerce_rule_type,
    coerce_severity,
)
from src.core.automod.manager.rules import _parse_action
from src.core.automod.manager.converters import row_to_rule, row_to_violation
from src.core.automod.exceptions import RuleValidationError


@pytest.mark.parametrize(
    "value", [RuleType.KEYWORD, "keyword", " regex ", "ai_moderation"]
)
def test_coerce_rule_type_accepts_allowlisted(value):
    assert isinstance(coerce_rule_type(value), RuleType)


@pytest.mark.parametrize("value", ["", "drop_database", None, 42, {"value": "keyword"}])
def test_coerce_rule_type_rejects_unknown(value):
    with pytest.raises(ValueError):
        coerce_rule_type(value)


@pytest.mark.parametrize("value", [ActionType.BAN_USER, "ban_user", " log_only "])
def test_coerce_action_type_accepts_allowlisted(value):
    assert isinstance(coerce_action_type(value), ActionType)


@pytest.mark.parametrize("value", ["rm_rf", "", None, 7, ["ban_user"]])
def test_coerce_action_type_rejects_unknown(value):
    with pytest.raises(ValueError):
        coerce_action_type(value)


def test_coerce_severity_default_and_allowlist():
    assert coerce_severity(None) is ViolationSeverity.MEDIUM
    assert coerce_severity("critical") is ViolationSeverity.CRITICAL
    with pytest.raises(ValueError):
        coerce_severity("apocalyptic")


def test_parse_action_requires_object_and_valid_type():
    with pytest.raises(RuleValidationError):
        _parse_action("not-an-object")
    with pytest.raises(RuleValidationError):
        _parse_action({"action_type": "nuke"})
    action = _parse_action({"type": "delete_message"})
    assert action.action_type is ActionType.DELETE_MESSAGE


def _rule_row(actions, rule_type="keyword"):
    return {
        "id": 1,
        "server_id": 2,
        "name": "r",
        "rule_type": rule_type,
        "enabled": 1,
        "config": "{}",
        "actions": actions,
        "applied_roles": "[]",
        "exempt_roles": "[]",
        "exempt_channels": "[]",
        "priority": 0,
        "created_at": 0,
        "updated_at": 0,
        "created_by": 3,
        "check_all": 0,
    }


def test_stored_unknown_actions_are_skipped_not_raised():
    import json

    row = _rule_row(
        json.dumps(
            [
                {"action_type": "delete_message"},
                {"action_type": "drop_tables"},
                "garbage",
            ]
        )
    )
    rule = row_to_rule(row)
    assert [a.action_type for a in rule.actions] == [ActionType.DELETE_MESSAGE]


def test_stored_unknown_severity_and_actions_fall_back():
    import json

    row = {
        "id": 1,
        "server_id": 2,
        "channel_id": 3,
        "user_id": 4,
        "message_id": 5,
        "rule_id": 6,
        "rule_type": "keyword",
        "matched_content": "x",
        "actions_taken": json.dumps(["ban_user", "not-real"]),
        "severity": "not-real",
        "created_at": 0,
        "metadata": "{}",
    }
    violation = row_to_violation(row)
    assert violation.actions_taken == [ActionType.BAN_USER]
    assert violation.severity is ViolationSeverity.MEDIUM
