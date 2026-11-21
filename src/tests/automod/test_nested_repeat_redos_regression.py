"""Regression test for ReDoS via nested bounded variable repeats.

RE2 rejects lookaheads/backreferences, so such a pattern runs on the stdlib
backtracking engine. The group-based vetting in ``_has_redos_risk`` only
checked each bounded repeat count against ``MAX_SAFE_NESTED_REPEAT`` (8), so
``(?=a*)((a{1,2}){8}){8}b`` passed vetting even though its ambiguity
multiplies to 2**(8*8) = 2**64 splits. A user message of ~64 ``a`` characters
stalled a worker for >2 seconds (and far longer near 96 characters).

The static analysis now caps the accumulated product of nested bounded
variable repeats, so the pattern is rejected at config-validation time and
never compiled into a matcher.
"""

import time

import pytest

from src.core.automod.models import ActionType, Rule, RuleAction, RuleType
from src.core.automod.rules import regex as regex_module
from src.core.automod.rules.regex import RegexRule, _has_redos_risk


PATHOLOGICAL_PATTERN = r"(?=a*)((a{1,2}){8}){8}b"


def _rule(config):
    return Rule(
        id=1,
        server_id=1,
        name="nested-repeat-redos",
        rule_type=RuleType.REGEX,
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

    class _NoopLogger:
        def error(self, *args, **kwargs):
            pass

        def warning(self, *args, **kwargs):
            pass

        def debug(self, *args, **kwargs):
            pass

        def info(self, *args, **kwargs):
            pass

    monkeypatch.setattr(regex_module, "HAS_RE2", False)
    monkeypatch.setattr(regex_module, "re2_module", None)
    monkeypatch.setattr(regex_module, "logger", _NoopLogger())


@pytest.mark.automod
class TestNestedBoundedRepeatRedos:
    @pytest.mark.parametrize(
        "pattern",
        [
            PATHOLOGICAL_PATTERN,
            r"(?=a*)((a?){8}){8}b",
            r"((a{1,2}){8}){8}b",
            r"((a{1,2}){5}){5}b",
            r"(?=a*)((a{1,3}){5}){5}b",
        ],
    )
    def test_nested_variable_repeats_are_flagged(self, pattern):
        assert _has_redos_risk(pattern), pattern

    @pytest.mark.parametrize(
        "pattern",
        [
            # Per-group bound stays at or below 8, single nesting level.
            r"(a{1,2}){8}b",
            # Fixed-length repetition and alternation of distinct literals.
            r"(a|b){2}",
            # IPv4 shape: bounded variable inner, one small outer repeat.
            r"(\d{1,3}\.){3}\d{1,3}",
            r"a{100}",
        ],
    )
    def test_safe_bounded_patterns_remain_accepted(self, pattern):
        assert not _has_redos_risk(pattern), pattern

    def test_validate_config_rejects_nested_repeat_redos(self, fallback_engine):
        ok, issues = RegexRule.validate_config(
            {"patterns": [{"pattern": PATHOLOGICAL_PATTERN, "name": "blocked"}]}
        )
        assert not ok
        assert any("rejected" in issue for issue in issues)

    def test_rule_never_compiles_nested_repeat_redos(self, fallback_engine):
        rule = RegexRule(
            _rule({"patterns": [{"pattern": PATHOLOGICAL_PATTERN, "name": "blocked"}]})
        )
        assert rule._compiled_patterns == []

    def test_check_is_bounded_on_pathological_input(self, fallback_engine):
        """The rejected pattern must never run on user-controlled text.

        Unfixed, a 2,000-character run of ``a`` followed by a non-matching
        character keeps the backtracking engine busy for far longer than the
        ceiling below. A safe rule is checked alongside it so an empty matcher
        cannot make the assertion vacuous.
        """
        bad = RegexRule(
            _rule({"patterns": [{"pattern": PATHOLOGICAL_PATTERN, "name": "blocked"}]})
        )
        good = RegexRule(
            _rule({"patterns": [{"pattern": r"a{8}b", "name": "allowed"}]})
        )
        started = time.monotonic()
        bad_result = bad.check("a" * 2000 + "!", 1, 1)
        elapsed = time.monotonic() - started
        assert elapsed < 1.5, f"check took {elapsed:.3f}s"
        assert not bad_result.matched
        assert good.check("aaaaaaaab", 1, 1).matched
