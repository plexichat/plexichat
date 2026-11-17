"""Regex compile budgets and quantifier bounds."""

from src.core.automod.limits import (
    MAX_CODE_LENGTH,
    MAX_PATTERNS_PER_RULE,
    MAX_REPEATS,
    MAX_TOTAL_PATTERN_CHARS,
)
from src.core.automod.models import Rule, RuleType
from src.core.automod.rules.links import InviteLinkRule
from src.core.automod.rules.regex import MAX_PATTERN_LENGTH, RegexRule
from src.core.automod.rules.repeated import RepeatedCharsRule


def _rule(config, rule_type=RuleType.REGEX):
    return Rule(
        id=1,
        server_id=2,
        name="r",
        rule_type=rule_type,
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


def test_pattern_count_cap_enforced():
    config = {
        "patterns": [{"pattern": "abc"} for _ in range(MAX_PATTERNS_PER_RULE + 1)]
    }
    ok, issues = RegexRule.validate_config(config)
    assert not ok and any("exceeds maximum" in i for i in issues)
    rule = RegexRule(_rule(config))
    assert len(rule._compiled_patterns) <= MAX_PATTERNS_PER_RULE


def test_total_compile_budget_enforced():
    config = {
        "patterns": [
            {"pattern": "x" * 400} for _ in range(MAX_TOTAL_PATTERN_CHARS // 400 + 1)
        ]
    }
    ok, issues = RegexRule.validate_config(config)
    assert not ok and any("budget" in i for i in issues)


def test_pattern_name_is_truncated():
    rule = RegexRule(_rule({"patterns": [{"pattern": "abc", "name": "n" * 500}]}))
    assert len(rule._compiled_patterns[0][2]) <= 128


def test_non_string_pattern_rejected():
    ok, issues = RegexRule.validate_config({"patterns": [{"pattern": 12345}]})
    assert not ok and issues


def test_invite_custom_pattern_gets_same_guards():
    ok, issues = InviteLinkRule.validate_config(
        {"pattern": "a" * (MAX_PATTERN_LENGTH + 1)}
    )
    assert not ok and issues
    ok, issues = InviteLinkRule.validate_config({"pattern": r"(a+)+$"})
    assert not ok and issues
    ok, issues = InviteLinkRule.validate_config({"code_length": MAX_CODE_LENGTH + 1})
    assert not ok and issues


def test_repeated_rule_quantifier_bounded():
    ok, issues = RepeatedCharsRule.validate_config({"max_repeats": MAX_REPEATS + 1})
    assert not ok and issues
    rule = RepeatedCharsRule(_rule({"max_repeats": 10**9}, RuleType.REPEATED_CHARS))
    assert rule._max_repeats <= MAX_REPEATS
    assert "1000000000" not in rule._pattern.pattern
