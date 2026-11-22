"""Regression: the stored DM-filter glob matcher must not rebuild its regex set.

``_linear_glob_match`` runs for every recipient pattern-filter row on every
scanned message.  The filter row (and therefore its ``*``-separated segment
regexes) is recipient configuration that is stable across thousands of
messages, but the helper used to call ``fnmatch.translate`` + ``re.compile``
for every segment on every call.  That is pure repeated work on the send path.

The test counts ``re.compile`` calls carrying the exact translated segment
patterns.  It is deterministic (unlike a wall-clock threshold) and fails on
the unfixed code, where every message rebuilds the whole segment pattern set.
"""

import fnmatch
import re

import src.core.antispam.detector as detector_mod
from src.core.antispam.detector import (
    _glob_segments,
    _linear_glob_match,
    _safe_fnmatchcase,
)

# A stored glob with two wildcards routes through the linear matcher and yields
# several segment regexes.  It is deliberately distinct from the fixtures in
# the other antispam tests so no other test warms its cache first.
_STORED_GLOB = "*free-crypto-giveaway* win-a-prize*"

_TEXT = "get a free-crypto-giveaway today and win-a-prize"


def _segment_sources(pattern: str) -> list[str]:
    """Exact regex sources the linear matcher compiles for ``pattern``."""
    sources = []
    for part in _glob_segments(pattern):
        if not part:
            continue
        translated = fnmatch.translate(part)
        sources.append(translated.removesuffix(r"\z").removesuffix(r"\Z"))
    return sources


def test_stored_glob_segments_compiled_once(monkeypatch):
    expected_sources = set(_segment_sources(_STORED_GLOB))
    assert expected_sources, "the fixture glob must have at least one segment"

    real_compile = re.compile
    compiled = []

    def counting_compile(pattern, *args, **kwargs):
        if isinstance(pattern, str) and pattern in expected_sources:
            compiled.append(pattern)
        return real_compile(pattern, *args, **kwargs)

    monkeypatch.setattr(detector_mod.re, "compile", counting_compile)

    # First message is allowed to build the segment pattern set.
    assert _linear_glob_match(_TEXT, _STORED_GLOB) is True
    assert compiled, "the cold path must compile the segment regexes"
    compiled.clear()

    # Later messages against the same stored pattern must reuse it.
    for _ in range(50):
        assert _linear_glob_match(_TEXT, _STORED_GLOB) is True
    assert compiled == [], (
        f"glob segment regexes recompiled {len(compiled)} times across messages; "
        "compile the segment pattern set once per stored glob"
    )


def test_cached_segments_preserve_match_semantics():
    cases = [
        ("*free crypto*", "get free crypto now", True),
        ("*banned*word*", "banned then word", True),
        ("*banned*word*", "nothing here", False),
        ("a?c*", "abcde", True),
        ("a?c*", "acde", False),
        ("[a-c]*x", "bqx", True),
        ("[!a]*x", "ax", False),
    ]
    for pattern, text, expected in cases:
        assert _safe_fnmatchcase(text, pattern) is expected, (pattern, text)


def test_cached_segments_keep_linear_time_glob():
    # ReDoS payload: 24 ``*a`` groups plus a trailing ``*b``.  A backtracking
    # matcher explores exponentially many splits of a run of 'a's with no 'b';
    # the linear matcher (now with cached segment regexes) must stay instant.
    import time

    payload = "*a" * 24 + "*b"
    started = time.monotonic()
    assert _safe_fnmatchcase("a" * 2000, payload) is False
    assert time.monotonic() - started < 2.0
