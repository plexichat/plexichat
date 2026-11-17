"""Rule-side enum values are validated against the shared allowlists."""

from typing import cast

import pytest

from src.core.automod.actions.base import BaseAction
from src.core.automod.models import (
    ActionType,
    Rule,
    RuleAction,
    RuleType,
    Violation,
    ViolationSeverity,
)
from src.core.automod.rules.ai import AIModerationRule
from src.core.automod.rules.keyword import KeywordRule
from src.core.automod.rules.regex import RegexRule


def _rule(config):
    return Rule(
        id=1,
        server_id=2,
        name="r",
        rule_type=RuleType.KEYWORD,
        enabled=True,
        config=config,
        actions=[],
        applied_roles=[],
        exempt_roles=[],
        exempt_channels=[],
        priority=0,
        created_at=0,
        updated_at=0,
        created_by=3,
    )


@pytest.mark.parametrize("value", ["openai", "perspective", "custom", "shieldstral"])
def test_valid_ai_backends(value):
    rule = AIModerationRule(_rule({"backend": value}))
    assert rule._backend == value


@pytest.mark.parametrize("value", ["evil", "", None, 42])
def test_unknown_ai_backend_falls_back(value):
    rule = AIModerationRule(_rule({"backend": value}))
    assert rule._backend == "shieldstral"


def test_ai_validate_config_rejects_unknown_backend():
    ok, issues = AIModerationRule.validate_config({"backend": "unknown"})
    assert not ok and issues


@pytest.mark.parametrize("value", ["low", "high", "critical", "garbage"])
def test_rule_severities_use_allowlist(value):
    expected = {
        "low": ViolationSeverity.LOW,
        "high": ViolationSeverity.HIGH,
        "critical": ViolationSeverity.CRITICAL,
    }.get(value, ViolationSeverity.MEDIUM)
    assert KeywordRule(_rule({}))._parse_severity(value) is expected
    assert RegexRule(_rule({}))._parse_severity(value) is expected


def test_base_action_refuses_unknown_action_type():
    class Dummy(BaseAction):
        def execute(self, action, violation, context=None):
            return True

    class FakeAction:
        action_type = "not-a-real-action"

    assert Dummy(None).can_execute(
        cast(RuleAction, FakeAction()), cast(Violation, None)
    ) == (False, "unknown action type")

    class GoodAction:
        action_type = ActionType.DELETE_MESSAGE

    assert Dummy(None).can_execute(
        cast(RuleAction, GoodAction()), cast(Violation, None)
    ) == (True, None)
