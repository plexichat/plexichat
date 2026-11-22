"""Regression: sibling bounded repeats / common-prefix alternations bypassed ReDoS vetting.

The other vetting checks cover nested quantifiers, unbounded wildcard chains
and flat unbounded-quantifier chains. They all miss two *sibling* shapes whose
atoms are only variably repeatable (not unbounded):

* ``a{4,5}a{4,5}...b`` -- each bounded repeat can consume 4 or 5 characters,
  so a run of ``a`` characters can be split into 4/5-sized chunks in
  exponentially many ways before the trailing ``b`` fails;
* ``(aaaa|aaaaa)(aaaa|aaaaa)...b`` -- each group has a common-prefix
  alternation, giving the same exponential split count.

Both are short enough to fit the 500-character pattern cap and both run on the
stdlib backtracking engine whenever a RE2-rejecting construct (e.g. a
backreference) is present, so they are reachable from the admin regex config.
The static analysis now chains adjacent overlapping variable-length atoms and
rejects these shapes before they can be compiled.
"""

import re
import signal

import pytest

from src.core.automod.models import ActionType, Rule, RuleAction, RuleType
from src.core.automod.rules import regex as regex_module
from src.core.automod.rules.regex import RegexRule, _has_redos_risk


# Large enough to be unambiguously catastrophic on the backtracking engine,
# still well under MAX_PATTERN_LENGTH (500).
_BOUNDED_REPEAT_PAYLOAD = "a{4,5}" * 32 + "b"
_ALTERNATION_PAYLOAD = "(aaaa|aaaaa)" * 32 + "b"
# RE2 rejects backreferences, so this suffix forces the vulnerable engine.
_FALLBACK_SUFFIX = "(a)\\1b"


def _rule(config):
    return Rule(
        id=1,
        server_id=1,
        name="sequential-ambiguous-redos",
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


class _Timeout(Exception):
    pass


def _on_alarm(signum, frame):
    raise _Timeout()


@pytest.mark.automod
class TestSequentialAmbiguousRepeatRedos:
    @pytest.mark.parametrize(
        "pattern",
        [
            _BOUNDED_REPEAT_PAYLOAD,
            _BOUNDED_REPEAT_PAYLOAD[:-1] + _FALLBACK_SUFFIX,
            _ALTERNATION_PAYLOAD,
            _ALTERNATION_PAYLOAD[:-1] + _FALLBACK_SUFFIX,
            r"a{1,100}a{1,100}a{1,100}b",  # wide bounded ranges, minimum chain
            r"(a|aa)(a|aa)(a|aa)b",  # prefix-overlapping alternation, minimum chain
            r"(a{4,5})(a{4,5})(a{4,5})b",  # grouped siblings stay flat
        ],
    )
    def test_sequential_ambiguous_repeats_are_flagged(self, pattern):
        assert _has_redos_risk(pattern), pattern

    @pytest.mark.parametrize(
        "pattern",
        [
            # Mandatory literal separators pin the repeats apart.
            r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}",
            r"\d{1,3}-\d{1,3}-\d{1,3}",
            # '?' and fixed bounds have no multi-way split ambiguity.
            r"a?a?a?a?b",
            r"a{2}a{2}a{2}b",
            # Disjoint leading atoms cannot share the same input.
            r"a{4,5}b{4,5}c{4,5}x",
            # Alternation branches that do not overlap are linear.
            r"(ab|cd)(ab|cd)(ab|cd)x",
        ],
    )
    def test_non_ambiguous_or_separated_patterns_remain_accepted(self, pattern):
        assert not _has_redos_risk(pattern), pattern
        assert re.compile(pattern) is not None

    def test_validate_config_rejects_sequential_ambiguous_redos(self, fallback_engine):
        ok, issues = RegexRule.validate_config(
            {"patterns": [{"pattern": _BOUNDED_REPEAT_PAYLOAD, "name": "blocked"}]}
        )
        assert not ok
        assert any("rejected" in issue for issue in issues)

        ok, issues = RegexRule.validate_config(
            {"patterns": [{"pattern": _ALTERNATION_PAYLOAD, "name": "blocked"}]}
        )
        assert not ok
        assert any("rejected" in issue for issue in issues)

    def test_rule_never_compiles_sequential_ambiguous_redos(self, fallback_engine):
        for payload in (_BOUNDED_REPEAT_PAYLOAD, _ALTERNATION_PAYLOAD):
            rule = RegexRule(
                _rule({"patterns": [{"pattern": payload, "name": "blocked"}]})
            )
            assert rule._compiled_patterns == []

    def test_raw_engine_is_catastrophic_on_the_unfixed_shape(self):
        """Prove the shapes are genuinely ReDoS, bounded by SIGALRM.

        On the stdlib engine each payload has exponentially many ways to split
        its input before failing on the trailing ``b``. The alarm bounds this
        proof; the rejected rule must never reach this code path.
        """
        old_handler = signal.signal(signal.SIGALRM, _on_alarm)
        try:
            for payload, chars in (
                (_BOUNDED_REPEAT_PAYLOAD, "a" * 200),
                (_ALTERNATION_PAYLOAD, "a" * 200),
            ):
                compiled = re.compile(payload)
                signal.setitimer(signal.ITIMER_REAL, 1.0)
                try:
                    compiled.search(chars + "!")
                except _Timeout:
                    pass  # expected: the unfixed shape is catastrophic
                else:
                    pytest.fail(
                        f"payload did not backtrack as expected: {payload[:40]}"
                    )
                finally:
                    signal.setitimer(signal.ITIMER_REAL, 0)
        finally:
            signal.signal(signal.SIGALRM, old_handler)
