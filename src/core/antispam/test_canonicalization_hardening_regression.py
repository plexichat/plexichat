"""Regression tests for DM-spam canonicalization hardening.

Covers two real weaknesses in ``_canonical_text``:

1. Invisible-character bypass (control-char handling / normalization bypass):
   the old denylist only stripped five zero-width characters. Injecting other
   invisible Unicode (bidi overrides, direction marks, soft hyphen, NUL,
   word joiners, ...) produced a different canonical form for a visually
   identical message, evading duplicate counters, glob filters and the
   invite/mention heuristics.

2. NFKC expansion defeated the pre-canonicalization length cap: a single
   compatibility character can expand ~18x, so a 10k-character message could
   become a ~180k-character canonical string fed to hashing/matching.

3. Whitespace/invisible ordering bypass: the old code collapsed whitespace
   BEFORE stripping invisibles. Since ZWSP/bidi marks are not ``\\s``, splicing
   one between two spaces left a *double* space in the canonical form after
   the invisible was removed. A spammer could vary that invisible to mint
   unlimited distinct hashes/globs for a visually identical message, evading
   duplicate counters and recipient pattern filters. Stripping invisibles
   first makes the canonical form identical again.

These tests FAIL on pre-fix code and PASS with the reordered canonicalizer.
"""

from __future__ import annotations

import pytest

import src.core.antispam.detector as detector_mod
from src.core.antispam.detector import DMSpamDetector, _canonical_text

pytestmark = pytest.mark.integration

RECIPIENT = 900


class _FakeDB:
    """Minimal DB stub: the detector only needs these calls for the checks."""

    def fetch_all(self, *args, **kwargs):
        return []

    def fetch_one(self, *args, **kwargs):
        return None

    def execute(self, *args, **kwargs):
        class _Cursor:
            rowcount = 1

        return _Cursor()

    def begin_transaction(self):
        pass

    def commit(self):
        pass

    def rollback(self):
        pass


@pytest.fixture(autouse=True)
def _local_counters(monkeypatch):
    # Force the process-local fallback so counters are deterministic and
    # resettable regardless of whether a Valkey server is reachable.
    monkeypatch.setattr(detector_mod, "get_client", lambda: None)
    monkeypatch.setattr(detector_mod, "is_available", lambda: False)
    # Avoid the snowflake generator (and its logger dependency) in event
    # recording; the ID value is irrelevant to these checks.
    monkeypatch.setattr(detector_mod, "generate_id", lambda: 1)
    detector_mod.reset_local_windows()
    yield
    detector_mod.reset_local_windows()


def test_canonical_text_strips_all_invisible_controls():
    base = _canonical_text("buy crypto now")
    # LRM, RLO, soft hyphen, NUL and word joiner are all invisible to a human
    # but were previously left in the canonical form.
    variant = _canonical_text("buy\u200e crypto\u202e now\u00ad\x00\u2060")
    assert variant == base


def test_duplicate_counter_not_bypassed_by_invisible_variants():
    detector = DMSpamDetector(_FakeDB())
    base = "buy crypto now"
    for _ in range(DMSpamDetector.DEFAULT_DUPLICATE_THRESHOLD):
        assert detector.check_message(1, RECIPIENT, base)[0] is True

    allowed, reason = detector.check_message(1, RECIPIENT, "buy\u200e crypto\u202e now")
    assert allowed is False
    assert reason == "Duplicate message detected"


def test_pattern_filter_not_bypassed_by_invisible_variants(monkeypatch):
    detector = DMSpamDetector(_FakeDB())
    monkeypatch.setattr(
        detector,
        "_get_user_filters",
        lambda recipient, sender: [
            {"filter_type": "pattern", "pattern": "*spam*", "action": "block"}
        ],
    )
    allowed, reason = detector.check_message(1, RECIPIENT + 1, "sp\u200eam here")
    assert allowed is False
    assert reason == "Message matched your DM spam filter"


def test_invite_heuristic_not_bypassed_by_invisible_variants():
    detector = DMSpamDetector(_FakeDB())
    content = "discord\u200e.gg/a discord\u202e.gg/b discord\u00ad.gg/c"
    allowed, reason = detector.check_message(1, RECIPIENT + 2, content)
    assert allowed is False
    assert reason == "Message flagged as potential spam"


def test_canonical_length_bounded_after_nfkc_expansion():
    # U+FDFA NFKC-expands to 18 codepoints; 1000 of them used to yield a
    # ~18,000-character canonical string despite the 10k input cap.
    canonical = _canonical_text("\ufdfa" * 1000)
    assert len(canonical) <= detector_mod.MAX_DM_CONTENT_LENGTH


# --- Regression: invisible characters adjacent to whitespace ----------------


@pytest.mark.parametrize(
    "variant",
    [
        "buy \u200b crypto \u200b now",  # ZWSP between spaces
        "buy \u200e crypto now",  # LRM next to a space
        "buy\t\u200b crypto now",  # tab followed by invisible
        "buy \u2060 crypto now",  # word joiner
    ],
)
def test_canonical_text_folds_invisible_next_to_whitespace(variant):
    """An invisible between spaces must not survive as a doubled space."""
    assert _canonical_text(variant) == _canonical_text("buy crypto now")


def test_duplicate_counter_not_bypassed_by_invisible_adjacent_to_whitespace():
    """Visually identical spam with spliced invisibles still trips dedup."""
    detector = DMSpamDetector(_FakeDB())
    base = "buy crypto now"
    for _ in range(DMSpamDetector.DEFAULT_DUPLICATE_THRESHOLD):
        assert detector.check_message(11, RECIPIENT, base)[0] is True

    # Each variant used to hash differently because the canonical form kept a
    # double space; after the fix all of them fold to the base form.
    for variant in (
        "buy \u200b crypto now",
        "buy \u200e crypto \u200b now",
        "buy\t\u2060 crypto now",
    ):
        allowed, reason = detector.check_message(11, RECIPIENT, variant)
        assert allowed is False
        assert reason == "Duplicate message detected"


def test_pattern_filter_not_bypassed_by_invisible_adjacent_to_whitespace(monkeypatch):
    """A glob that matches the plain text must also match spaced invisibles."""
    detector = DMSpamDetector(_FakeDB())
    monkeypatch.setattr(
        detector,
        "_get_user_filters",
        lambda recipient, sender: [
            {"filter_type": "pattern", "pattern": "*buy crypto now*", "action": "block"}
        ],
    )
    allowed, reason = detector.check_message(
        12, RECIPIENT + 3, "buy \u200b crypto \u200e now"
    )
    assert allowed is False
    assert reason == "Message matched your DM spam filter"
