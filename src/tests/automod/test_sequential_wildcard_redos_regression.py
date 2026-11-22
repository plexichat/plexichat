"""Regression: flat wildcard chains (glob-style patterns) are ReDoS.

A stored automod pattern such as ``.*a.*a.*a.*(b)`` has no nested quantifiers,
so the group-based analysis in ``_has_redos_risk`` used to let it through. Both
consumers of that analysis then compiled it with the stdlib backtracking
engine -- ``RegexRule`` whenever RE2 is unavailable, and ``InviteLinkRule``
unconditionally -- and ran it against user-controlled message text.

Measured before the fix (bounded probe with SIGALRM): a 15-character pattern
took ~0.96s on a 100-character run of ``a`` and exceeded 3s on 200 characters;
800 characters exceeded 60s. The attacker only needs a stored pattern plus a
short message to stall a shared worker.

The fix rejects patterns with three or more broad unbounded quantifiers at the
same nesting level, so the pattern never reaches the backtracking engine.
"""

import re
import time

import pytest

from src.core.automod.models import ActionType, Rule, RuleAction, RuleType
from src.core.automod.rules import regex as regex_module
from src.core.automod.rules.links import InviteLinkRule
from src.core.automod.rules.regex import RegexRule, _has_redos_risk

# 15 characters, well under MAX_PATTERN_LENGTH (500).
_PAYLOAD = r".*a.*a.*a.*(b)"
_PAYLOAD_TEXT = "a" * 100


class _NoopLogger:
    def error(self, *args, **kwargs):
        pass

    def warning(self, *args, **kwargs):
        pass

    def debug(self, *args, **kwargs):
        pass

    def info(self, *args, **kwargs):
        pass


@pytest.fixture
def fallback_engine(monkeypatch):
    """Force the stdlib backtracking engine -- the vulnerable path."""
    monkeypatch.setattr(regex_module, "HAS_RE2", False)
    monkeypatch.setattr(regex_module, "re2_module", None)
    monkeypatch.setattr(regex_module, "logger", _NoopLogger())


def _rule(rule_type, config):
    return Rule(
        id=1,
        server_id=1,
        name="sequential-wildcard",
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


@pytest.mark.automod
class TestSequentialWildcardRedos:
    @pytest.mark.parametrize(
        "pattern",
        [
            _PAYLOAD,
            # The fnmatch-glob shape: a chain of ``.*a`` groups plus ``.*b``.
            ".*a" * 24 + ".*b",
            # Negated classes and wide shorthand classes are equally ambiguous.
            r"[^ ]+a[^ ]+a[^ ]+b",
            r"\w+a\w+a\w+b",
        ],
    )
    def test_pathological_wildcard_chain_is_flagged(self, pattern):
        assert _has_redos_risk(pattern), pattern

    @pytest.mark.parametrize(
        "pattern",
        [
            r".*a.*b",  # two wildcards stay cheap
            r".*error.*",  # literal separators the dot cannot absorb ambiguity with
            r"<a?:\w+:\d+>",  # emoji shape
            r"https?://[^\s]+",  # URL shape
            r"\d+-\d+-\d+",  # narrow atoms, no overlap with separators
            r"(\d{1,3}\.){3}\d{1,3}",  # IPv4 shape
        ],
    )
    def test_legitimate_patterns_remain_accepted(self, pattern):
        assert not _has_redos_risk(pattern), pattern
        # The accepted pattern must still compile and match.
        assert re.compile(pattern) is not None

    def test_regex_rule_never_compiles_wildcard_chain(self, fallback_engine):
        rule = RegexRule(
            _rule(RuleType.REGEX, {"patterns": [{"pattern": _PAYLOAD, "name": "spam"}]})
        )
        assert rule._compiled_patterns == []

    def test_regex_rule_validate_config_rejects_wildcard_chain(self, fallback_engine):
        ok, issues = RegexRule.validate_config(
            {"patterns": [{"pattern": _PAYLOAD, "name": "spam"}]}
        )
        assert not ok
        assert any("rejected" in issue for issue in issues)

    def test_legitimate_regex_rule_still_matches(self, fallback_engine):
        rule = RegexRule(
            _rule(
                RuleType.REGEX,
                {"patterns": [{"pattern": r".*free.*crypto", "name": "crypto"}]},
            )
        )
        assert len(rule._compiled_patterns) == 1
        assert rule.check("get free crypto now", 1, 1).matched is True

    def test_invite_rule_falls_back_to_safe_default(self):
        # InviteLinkRule compiles its custom pattern with stdlib re on *every*
        # engine path, so this rejection matters even when RE2 is installed.
        rule = InviteLinkRule(_rule(RuleType.INVITE_LINKS, {"pattern": _PAYLOAD}))
        assert rule._pattern is InviteLinkRule.DEFAULT_INVITE_PATTERN
        # Legitimate invite codes are still detected by the safe default.
        assert rule._pattern.findall("join AbCdEfGh here") == ["AbCdEfGh"]

    def test_check_is_bounded_on_pathological_input(self, fallback_engine):
        regex_rule = RegexRule(
            _rule(RuleType.REGEX, {"patterns": [{"pattern": _PAYLOAD, "name": "spam"}]})
        )
        invite_rule = InviteLinkRule(
            _rule(RuleType.INVITE_LINKS, {"pattern": _PAYLOAD})
        )

        started = time.monotonic()
        assert not regex_rule.check(_PAYLOAD_TEXT, 1, 1).matched
        assert not invite_rule.check(_PAYLOAD_TEXT, 1, 1).matched
        elapsed = time.monotonic() - started
        assert elapsed < 1.5, f"check took {elapsed:.3f}s"


# A second, distinct shape the sequential-wildcard check misses: siblings of
# unbounded quantifiers over NARROW atoms that can all consume the same input.
# Measured before this fix (bounded probe): ``a*a*a*a*a*b`` took ~0.5s on 120
# characters and timed out (>3s) at 160. ``InviteLinkRule`` uses stdlib ``re``
# for custom patterns on every engine path, so this reached production even
# with RE2 installed.
_FLAT_PAYLOAD = r"a*a*a*a*a*b"
_FLAT_PAYLOAD_TEXT = "a" * 120


@pytest.mark.automod
class TestFlatQuantifierChainRedos:
    @pytest.mark.parametrize(
        "pattern",
        [
            _FLAT_PAYLOAD,
            r"a+a+a+a+b",  # '+' instead of '*'
            r"\d*\d*\d*\d*x",  # digit shorthands overlap each other
            r"[a]*[a]*[a]*b",  # positive classes overlap each other
            r"a{1,}a{1,}a{1,}b",  # unbounded {n,} form
            r"(a*)(a*)(a*)(a*)b",  # grouped siblings are still flat
            r"a*a*a*b",  # minimum: three overlapping quantifiers
        ],
    )
    def test_pathological_flat_chain_is_flagged(self, pattern):
        assert _has_redos_risk(pattern), pattern

    @pytest.mark.parametrize(
        "pattern",
        [
            r"ab*ab*ab*ab*b",  # b* pinned apart by mandatory 'a' separators
            r"\d+-\d+-\d+",  # '-' separators are not consumable by \d
            r".*a.*b",  # only two wildcards stay cheap
            r"a?a?a?a?b",  # bounded quantifiers, no ambiguity
            r"\s*\d*x",  # disjoint atoms cannot share input
            r"(\d{1,3}\.){3}\d{1,3}",  # IPv4 shape
        ],
    )
    def test_non_overlapping_or_short_chains_remain_accepted(self, pattern):
        assert not _has_redos_risk(pattern), pattern
        assert re.compile(pattern) is not None

    def test_regex_rule_never_compiles_flat_chain(self, fallback_engine):
        rule = RegexRule(
            _rule(
                RuleType.REGEX,
                {"patterns": [{"pattern": _FLAT_PAYLOAD, "name": "spam"}]},
            )
        )
        assert rule._compiled_patterns == []

    def test_regex_rule_validate_config_rejects_flat_chain(self, fallback_engine):
        ok, issues = RegexRule.validate_config(
            {"patterns": [{"pattern": _FLAT_PAYLOAD, "name": "spam"}]}
        )
        assert not ok
        assert any("rejected" in issue for issue in issues)

    def test_invite_rule_falls_back_to_safe_default(self):
        # InviteLinkRule compiles custom patterns with stdlib re even when RE2
        # is installed, so rejecting the flat chain here matters in prod.
        rule = InviteLinkRule(_rule(RuleType.INVITE_LINKS, {"pattern": _FLAT_PAYLOAD}))
        assert rule._pattern is InviteLinkRule.DEFAULT_INVITE_PATTERN
        assert rule._pattern.findall("join AbCdEfGh here") == ["AbCdEfGh"]

    def test_legitimate_regex_rule_still_matches(self, fallback_engine):
        rule = RegexRule(
            _rule(
                RuleType.REGEX,
                {"patterns": [{"pattern": r"\d+-\d+-\d+", "name": "dashed"}]},
            )
        )
        assert len(rule._compiled_patterns) == 1
        assert rule.check("order 12-34-56", 1, 1).matched is True

    def test_check_is_bounded_on_pathological_input(self, fallback_engine):
        regex_rule = RegexRule(
            _rule(
                RuleType.REGEX,
                {"patterns": [{"pattern": _FLAT_PAYLOAD, "name": "spam"}]},
            )
        )
        invite_rule = InviteLinkRule(
            _rule(RuleType.INVITE_LINKS, {"pattern": _FLAT_PAYLOAD})
        )

        started = time.monotonic()
        assert not regex_rule.check(_FLAT_PAYLOAD_TEXT, 1, 1).matched
        assert not invite_rule.check(_FLAT_PAYLOAD_TEXT, 1, 1).matched
        elapsed = time.monotonic() - started
        assert elapsed < 1.5, f"check took {elapsed:.3f}s"
