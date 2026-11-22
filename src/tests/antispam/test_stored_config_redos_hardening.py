"""Regression coverage for two stored-configuration weaknesses.

1. DM spam pattern filters are recipient-controlled JSON rows that are
   reloaded on every worker start. They were matched with
   ``fnmatch.fnmatchcase``, whose glob-to-regex translation builds a chain of
   greedy ``.*`` quantifiers. A pattern such as ``*a*a*...*b`` therefore made
   the stdlib backtracking engine explore an exponential number of splits of
   the message: any user could stall a shared worker for every message sent to
   them. Matching now uses a linear-time glob algorithm with identical
   ``*`` / ``?`` / ``[seq]`` semantics.

2. MassEmojiRule read ``max_percentage`` / ``min_length`` / ``count_*`` raw
   from the stored rule config at construction. Stored rows bypass
   ``validate_config()``, so a stale or hand-edited row carrying a string,
   ``None`` or an unreachable length either raised ``TypeError`` inside
   ``check()`` (taking down the moderation pipeline) or silently disabled the
   rule. Those fields are now bounded at construction as well.
"""

import fnmatch
import math
import time

import pytest

import src.core.antispam.detector as detector_mod
from src.core.antispam.detector import (
    DMSpamDetector,
    _safe_fnmatchcase,
    reset_local_windows,
)
from src.core.automod.limits import MAX_EMOJI_COUNT, MAX_MIN_LENGTH
from src.core.automod.models import (
    ActionType,
    Rule,
    RuleAction,
    RuleType,
)
from src.core.automod.rules.emoji import MassEmojiRule


@pytest.fixture(autouse=True)
def _clean_counters():
    reset_local_windows()
    yield
    reset_local_windows()


# ---------------------------------------------------------------------------
# 1. Glob matching must stay fnmatch-equivalent and ReDoS-safe
# ---------------------------------------------------------------------------

_GLOB_CASES = [
    # The detector matches its casefolded canonical text against a casefolded
    # pattern, so this row feeds the matcher the same lowercased pair; the
    # fnmatch oracle above is case-sensitive and would otherwise disagree.
    ("*free crypto*", "get free crypto now", True),
    ("*free crypto*", "nothing here", False),
    ("banned-word*", "banned-word here", True),
    ("*banned-word", "here banned-word", True),
    ("a?c", "abc", True),
    ("a?c", "ac", False),
    ("[a-c]x", "bx", True),
    ("[a-c]x", "zx", False),
    ("[!a]x", "bx", True),
    ("[!a]x", "ax", False),
    ("[]]", "]", True),
    ("[abc", "[abc", True),
    ("", "", True),
    ("", "x", False),
    ("*", "", True),
    ("*", "anything\nwith newline", True),
    ("?a*", "XaY", True),
    ("a\\b", "a\\b", True),  # backslash is literal on POSIX
]


@pytest.mark.parametrize("pattern,text,expected", _GLOB_CASES)
def test_safe_glob_matches_fnmatch_semantics(pattern, text, expected):
    # Sanity-check the oracle itself for the cases we assert on.
    assert fnmatch.fnmatchcase(text, pattern) is expected
    assert _safe_fnmatchcase(text, pattern) is expected


_GLOB_FUZZ_PATTERNS = [
    "*a*b",
    "a*b*c",
    "?a?b*",
    "[a-z]*[0-9]",
    "*[!x]z*",
    "a**b",
    "*a*a*a*b",
    "*?*?*",
    "*[]]*",
    "*a\\b*",
]


@pytest.mark.parametrize("pattern", _GLOB_FUZZ_PATTERNS)
@pytest.mark.parametrize(
    "text",
    ["", "a", "ab", "aXb", "aaab", "a1z", "xyz", "axb", "a\\b", "zz9"],
)
def test_safe_glob_agrees_with_fnmatch_on_wildcard_shapes(pattern, text):
    assert _safe_fnmatchcase(text, pattern) == fnmatch.fnmatchcase(text, pattern)


def test_stored_glob_payload_does_not_backtrack_exponentially():
    # Concrete ReDoS payload: 24 ``*a`` groups plus a trailing ``*b``. On a
    # run of 'a's with no 'b', fnmatch's compiled ``.*a.*a...`` chain explores
    # exponentially many partitions. The linear matcher must resolve it
    # immediately and (correctly) report no match.
    payload_pattern = "*a" * 24 + "*b"
    payload_text = "a" * 2000
    started = time.monotonic()
    assert _safe_fnmatchcase(payload_text, payload_pattern) is False
    assert time.monotonic() - started < 2.0


class _FakeDB:
    """Minimal DB double: returns one pattern filter, records no events."""

    def __init__(self, pattern):
        self.filter_row = {
            "id": 1,
            "user_id": 2,
            "target_user_id": None,
            "pattern": pattern,
            "filter_type": "pattern",
            "action": "block",
            "threshold": 10,
            "window_seconds": 60,
            "enabled": 1,
        }

    def fetch_all(self, query, params=()):
        if "dm_spam_filters" in query:
            return [dict(self.filter_row)]
        return []

    def fetch_one(self, query, params=()):
        return None  # sender is not a "new user"

    def execute(self, query, params=()):
        return None


class _NoopCounter:
    def configure(self, db):
        return None

    def record_and_count(self, key, now_s, window_s, limit):
        return True


def test_detector_pattern_filter_uses_safe_glob_on_stored_payload(monkeypatch):
    # End-to-end: a stored pattern filter must not hang check_message().
    payload = "*a" * 24 + "*b"
    monkeypatch.setattr(detector_mod, "_COUNTER", _NoopCounter())
    detector = DMSpamDetector(_FakeDB(payload))
    started = time.monotonic()
    assert detector.check_message(1, 2, "a" * 2000) == (True, None)
    assert time.monotonic() - started < 2.0


# ---------------------------------------------------------------------------
# 2. Stored emoji-rule config must be bounded at construction
# ---------------------------------------------------------------------------


def _emoji_rule(config):
    return MassEmojiRule(
        Rule(
            id=1,
            server_id=1,
            name="emoji",
            rule_type=RuleType.MASS_EMOJI,
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
    )


def test_stored_emoji_config_strings_do_not_crash_or_disable_checks():
    # Concrete payloads that used to reach check() raw: the string percentage
    # raised TypeError at `emoji_percentage > "50%"`, the string min_length at
    # `total_length < "9999"`. Either exception propagated out of the whole
    # message-moderation pipeline.
    rule = _emoji_rule(
        {
            "max_emoji": "lots",
            "max_percentage": "50%",
            "min_length": "9999",
            "count_custom": "yes",
            "count_unicode": 1,
        }
    )
    assert rule._max_emoji == 10
    assert rule._max_percentage == 50.0
    assert rule._min_length == 5
    assert rule._count_custom is True
    assert rule._count_unicode is True

    wall = "\U0001f600" * 20
    match = rule.check(wall, 1, 1)
    assert match.matched is True
    assert match.match_details["total_emoji"] == 20


def test_stored_emoji_config_nan_and_out_of_range_values_are_clamped():
    rule = _emoji_rule(
        {
            "max_percentage": math.nan,
            "max_emoji": -5,
        }
    )
    assert rule._max_percentage == 50.0
    assert rule._max_emoji == 10  # out-of-range value falls back to the default
    # NaN used to make `emoji_percentage > nan` silently False, disabling the
    # percentage path for every message. Clamped to the finite default again.
    assert rule.check("\U0001f600" * 20, 1, 1).matched is True


def test_stored_emoji_min_length_is_capped_at_shared_ceiling():
    # 10**9 used to suppress the rule for every realistic message. It is now
    # clamped to the same MAX_MIN_LENGTH ceiling the caps rule uses.
    rule = _emoji_rule({"min_length": 10**9})
    assert rule._min_length == MAX_MIN_LENGTH


def test_validate_config_rejects_out_of_range_stored_values():
    assert MassEmojiRule.validate_config({"min_length": 10**9})[0] is False
    assert MassEmojiRule.validate_config({"min_length": 0})[0] is False
    assert MassEmojiRule.validate_config({"max_percentage": math.nan})[0] is False
    assert MassEmojiRule.validate_config({"max_percentage": True})[0] is False
    assert MassEmojiRule.validate_config({"max_emoji": MAX_EMOJI_COUNT + 1})[0] is False
