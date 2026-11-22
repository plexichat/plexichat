"""Regression coverage for the stored DM-filter glob ReDoS fix.

Recipient-stored ``dm_spam_filters`` pattern rows are attacker-controlled and
reloaded on every worker start. They used to be matched with
``fnmatch.fnmatchcase``, whose glob-to-regex translation turns every ``*``
into a greedy ``.*``; a shape such as ``*a*...*b`` on a run of ``a`` then made
the backtracking engine explore exponentially many splits of the message.

``_safe_fnmatchcase`` now vets the stored glob: shapes with two or more ``*``
groups go to a linear two-pointer matcher, while zero/one-star patterns keep
the stdlib's exact (linear) semantics. This file covers both equivalence and
the non-vacuous timing/routing behaviour.
"""

import fnmatch
import time

import pytest

import src.core.antispam.detector as detector_mod
from src.core.antispam.detector import (
    DMSpamDetector,
    _has_glob_redos_risk,
    _safe_fnmatchcase,
    reset_local_windows,
)


@pytest.fixture(autouse=True)
def _clean_counters():
    reset_local_windows()
    yield
    reset_local_windows()


# fnmatchcase is case-sensitive; compare canonical (casefolded) inputs as the
# detector itself does before matching.
_GLOB_CASES = [
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
    assert fnmatch.fnmatchcase(text, pattern) is expected
    assert _safe_fnmatchcase(text, pattern) is expected


_FUZZ_SHAPES = [
    "*a*b",
    "a*b*c",
    "?a?b*",
    "[a-z]*[0-9]",
    "*[!x]z*",
    "a**b",
    "*a*a*a*b",
    "*?*?*",
    "*[]]*",
    "*[*]?",  # '*' is a literal member inside a bracket expression
    "*a\\b*",
]


@pytest.mark.parametrize("pattern", _FUZZ_SHAPES)
@pytest.mark.parametrize(
    "text",
    ["", "a", "ab", "aXb", "aaab", "a1z", "xyz", "axb", "a\\b", "zz9", "[-"],
)
def test_safe_glob_agrees_with_fnmatch_on_wildcard_shapes(pattern, text):
    # These multi-star shapes are exactly the ones routed to the linear
    # matcher; equivalence with fnmatch must still hold.
    assert _has_glob_redos_risk(pattern) == (pattern.count("*") >= 2)
    assert _safe_fnmatchcase(text, pattern) == fnmatch.fnmatchcase(text, pattern)


def test_stored_glob_payload_is_linear():
    # The concrete ReDoS payload: 24 `*a` groups plus `*b`. The old glob->`.*`
    # translation needs exponentially many partitions of a run of 'a's with no
    # 'b'; the linear matcher resolves it in milliseconds.
    payload = "*a" * 24 + "*b"
    text = "a" * 5000
    assert _has_glob_redos_risk(payload) is True
    started = time.monotonic()
    assert _safe_fnmatchcase(text, payload) is False
    assert time.monotonic() - started < 2.0


def test_pathological_shape_never_reaches_fnmatch(monkeypatch):
    # Non-vacuous routing check: if a multi-star pattern reached fnmatch, the
    # patched matcher would explode. Only the <=1-star branch may call it.
    def explode(*args, **kwargs):  # pragma: no cover - must never be called
        raise AssertionError("pathological glob reached fnmatch.fnmatchcase")

    monkeypatch.setattr(detector_mod.fnmatch, "fnmatchcase", explode)
    assert _safe_fnmatchcase("a" * 2000, "*a" * 24 + "*b") is False
    assert _safe_fnmatchcase("a" * 2000, "*a*b*c") is False


def test_overlong_glob_is_rejected():
    # Defence in depth for rows that bypass the add-time 512-char cap.
    assert (
        _safe_fnmatchcase(
            "anything", "a" * (detector_mod.MAX_FILTER_PATTERN_LENGTH + 1)
        )
        is False
    )


class _FakeDB:
    """Minimal DB double: one stored pattern filter, no event writes."""

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
        return None

    def execute(self, query, params=()):
        return None


class _NoopCounter:
    def configure(self, db):
        return None

    def record_and_count(self, key, now_s, window_s, limit):
        return True


def test_detector_pattern_filter_uses_safe_glob_on_stored_payload(monkeypatch):
    # End-to-end: a stored pattern filter must not stall check_message().
    monkeypatch.setattr(detector_mod, "_COUNTER", _NoopCounter())
    detector = DMSpamDetector(_FakeDB("*a" * 24 + "*b"))
    started = time.monotonic()
    assert detector.check_message(1, 2, "a" * 5000) == (True, None)
    assert time.monotonic() - started < 2.0


def test_detector_still_matches_legitimate_stored_glob(monkeypatch):
    monkeypatch.setattr(detector_mod, "_COUNTER", _NoopCounter())
    detector = DMSpamDetector(_FakeDB("*free crypto*"))
    allowed, reason = detector.check_message(1, 2, "get free crypto now")
    assert allowed is False
    assert reason == "Message matched your DM spam filter"
