"""Regression tests for the bounded-quantifier ReDoS bypass in automod regex rules.

The fallback (stdlib ``re``) vetting used to treat every *finite* quantifier as
safe. A large bounded outer repetition over a variable-count inner atom, such
as ``(a{1,2}){100}``, is anything but safe: the engine can explore exponentially
many ways to split the same input (a few dozen ``a`` characters followed by a
non-matching character stall a worker for minutes). With no RE2 module
installed that pattern was compiled and run on every matching message.

These tests keep the bypass closed at every ingress (static analysis, config
validation and rule compilation) without breaking the legitimate small/bounded
patterns the vetting is meant to allow.
"""

import pytest

from src.core.automod.rules import regex as regex_module
from src.core.automod.rules.regex import RegexRule, _has_redos_risk
from src.core.automod.models import (
    ActionType,
    Rule,
    RuleAction,
    RuleType,
)


def _rule(rule_type, config):
    return Rule(
        id=1,
        server_id=1,
        name="t",
        rule_type=rule_type,
        enabled=True,
        config=config,
        actions=[RuleAction(action_type=ActionType.DELETE_MESSAGE)],
        applied_roles=[],
        exempt_roles=[],
        exempt_channels=[],
        priority=0,
        created_at=0,
        updated_at=0,
        created_by=0,
    )


@pytest.fixture
def fallback_engine(monkeypatch):
    """Force the stdlib backtracking engine (the vulnerable path)."""
    monkeypatch.setattr(regex_module, "HAS_RE2", False)
    monkeypatch.setattr(regex_module, "re2_module", None)


@pytest.mark.automod
class TestBoundedQuantifierRedoSBypass:
    @pytest.mark.parametrize(
        "pattern",
        [
            r"(a{1,2}){50}",
            r"(a{1,2}){100}",
            r"(a{1,2}){200}",
            r"(a?){20}",
            r"((a{1,2}){3}){50}",
            r"((a|aa)){50}",
        ],
    )
    def test_large_bounded_nested_repetition_is_flagged(self, pattern):
        assert _has_redos_risk(pattern), pattern

    @pytest.mark.parametrize(
        "pattern",
        [
            r"\b\d{16}\b",
            r"(\d{1,3}\.){3}\d{1,3}",
            r"(a|b){2}",
            r"(ab{2,4}c){3}",
            r"(a{2}){50}",
            r"[a-z]+(\d)?x*end",
        ],
    )
    def test_fixed_and_small_bounded_patterns_still_accepted(self, pattern):
        assert not _has_redos_risk(pattern), pattern

    def test_validate_config_rejects_bounded_redos(self, fallback_engine):
        ok, issues = RegexRule.validate_config(
            {"patterns": [{"pattern": r"(a{1,2}){100}", "name": "blocked"}]}
        )
        assert not ok
        assert any("rejected" in issue for issue in issues)

    def test_rule_never_compiles_bounded_redos_pattern(self, fallback_engine):
        rule = RegexRule(
            _rule(
                RuleType.REGEX,
                {"patterns": [{"pattern": r"(a{1,2}){100}", "name": "blocked"}]},
            )
        )
        assert rule._compiled_patterns == []
        assert rule._no_match() == rule.check("a" * 100 + "!", 1, 1)

    def test_safe_bounded_patterns_still_compile(self, fallback_engine):
        ok, issues = RegexRule.validate_config(
            {
                "patterns": [
                    {"pattern": r"(\d{1,3}\.){3}\d{1,3}", "name": "ipv4"},
                    {"pattern": r"(a{1,2}){3}", "name": "small"},
                ]
            }
        )
        assert ok, issues
        rule = RegexRule(
            _rule(
                RuleType.REGEX,
                {
                    "patterns": [
                        {"pattern": r"(\d{1,3}\.){3}\d{1,3}", "name": "ipv4"},
                        {"pattern": r"(a{1,2}){3}", "name": "small"},
                    ]
                },
            )
        )
        assert {p[2] for p in rule._compiled_patterns} == {"ipv4", "small"}
