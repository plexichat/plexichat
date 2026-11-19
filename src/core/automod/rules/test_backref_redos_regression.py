"""Regression test for catastrophic backtracking via quantified backreferences.

RE2 rejects backreferences, so a regex rule containing one runs on the stdlib
backtracking engine. The group-based ReDoS vetting in ``_has_redos_risk`` could
not see repetition that lives in a backreference: ``(a+)\\1+b`` passed vetting,
compiled, and then stalled a worker for >12s on a ~2,000 character message of
repeated ``a`` characters. The static analysis now rejects that shape so the
pattern never reaches the backtracking engine.
"""

import time

import pytest

from src.core.automod.models import ActionType, Rule, RuleAction, RuleType
from src.core.automod.rules import regex as regex_module
from src.core.automod.rules.regex import RegexRule, _has_redos_risk


def _rule(config):
    return Rule(
        id=1,
        server_id=1,
        name="backref-redos",
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
    # The rejection path logs an error; keep this test self-contained instead
    # of depending on the global logger configured by src/tests/_conftest.
    monkeypatch.setattr(regex_module, "logger", _NoopLogger())


@pytest.mark.automod
class TestQuantifiedBackreferenceRedos:
    @pytest.mark.parametrize(
        "pattern",
        [
            r"(a+)\1+b",
            r"((a)+)\1+",
            r"(a{1,2})\1+",
            r"(a|aa)\1+",
            r"(?=(a+))\1+b",
        ],
    )
    def test_quantified_backref_to_variable_group_is_flagged(self, pattern):
        assert _has_redos_risk(pattern), pattern

    @pytest.mark.parametrize(
        "pattern",
        [
            # Fixed-length capture: \1+ repeats a constant string, linear.
            r"(ab)\1+",
            r"(a)\1+",
            # Backreference is not repeated: also linear.
            r"\b(\w+)\s+\1\b",
        ],
    )
    def test_safe_backreferences_remain_accepted(self, pattern):
        assert not _has_redos_risk(pattern), pattern

    def test_validate_config_rejects_backref_redos(self, fallback_engine):
        ok, issues = RegexRule.validate_config(
            {"patterns": [{"pattern": r"(a+)\1+b", "name": "blocked"}]}
        )
        assert not ok
        assert any("rejected" in issue for issue in issues)

    def test_rule_never_compiles_backref_redos(self, fallback_engine):
        rule = RegexRule(
            _rule({"patterns": [{"pattern": r"(a+)\1+b", "name": "blocked"}]})
        )
        assert rule._compiled_patterns == []

    def test_pathological_input_has_bounded_check_time(self, fallback_engine):
        """The rejected pattern must never run on user-controlled text.

        The unvetted pattern needs >12s on a 2,000-character run of ``a``; with
        the fix in place the check is a no-op. The generous 1.5s ceiling leaves
        ample room for a slow CI runner while still catching a regression that
        reintroduces the catastrophic match.
        """
        rule = RegexRule(
            _rule({"patterns": [{"pattern": r"(a+)\1+b", "name": "blocked"}]})
        )
        started = time.monotonic()
        result = rule.check("a" * 2000 + "!", 1, 1)
        elapsed = time.monotonic() - started
        assert elapsed < 1.5, f"check took {elapsed:.3f}s"
        assert not result.matched
