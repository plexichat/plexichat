"""Regression tests for DM-spam detector bypass/weakness group (H6/M11/M12).

H6 (variant bypass): content/pattern matching must canonicalize inputs
    (NFKC + casefold + trim + zero-width removal) before matching, hashing,
    or counting, so trailing-space / case / Unicode variants cannot evade
    blocking. These tests FAIL on pre-fix code (exact-byte hashing and
    ``.lower()``-only matching let every variant through).

M11 (weak heuristics): bulk URL-only and mention-only bursts must be able
    to reach the block threshold, while a single benign URL and a single
    shared invite must still pass (the test_invite_link_alone_allowed
    contract). These tests FAIL on pre-fix code (URL-alone capped at +2,
    mention-alone capped at +2, so neither could ever block).

M12 (set_filter upsert race): scope keys must be canonicalized so spelling
    variants cannot fork duplicate rows, and NULL-safe UNIQUE guards must
    exist so a lost concurrent-insert race retries as an UPDATE instead of
    duplicating. The variant test FAILS on pre-fix code (``"Rate"`` vs
    ``" rate "`` forge distinct rows); the index test FAILS on pre-fix code
    (no guard indexes are ever created).

Implementation: src/core/antispam/detector.py (DMSpamDetector).
src/core/dmspam/ holds only the schema for these tables.
"""

from __future__ import annotations

import pytest

import src.core.antispam.detector as detector_mod
from src.core.antispam.detector import DMSpamDetector

pytestmark = pytest.mark.integration

T0_MS = 1_000_000_000_000


@pytest.fixture(autouse=True)
def clean_counters():
    detector_mod.reset_local_windows()
    yield
    detector_mod.reset_local_windows()


def _frozen_detector(db):
    detector = DMSpamDetector(db)
    state = {"now": T0_MS}

    def _ts():
        return state["now"]

    detector._get_timestamp = _ts
    detector._tick = lambda ms: state.update(now=state["now"] + ms)
    return detector


class TestVariantBypassH6:
    def test_duplicate_ignores_case_and_padding_variants(self, db):
        detector = _frozen_detector(db)
        base = "buy crypto now"
        for _ in range(DMSpamDetector.DEFAULT_DUPLICATE_THRESHOLD):
            assert detector.check_message(1, 200, base)[0] is True
        # Case + trailing-space variant of the same spam must still trip.
        allowed, reason = detector.check_message(1, 200, "BUY CRYPTO NOW   ")
        assert allowed is False
        assert reason == "Duplicate message detected"

    def test_duplicate_ignores_fullwidth_unicode_variants(self, db):
        detector = _frozen_detector(db)
        base = "buy crypto now"
        for _ in range(DMSpamDetector.DEFAULT_DUPLICATE_THRESHOLD):
            assert detector.check_message(1, 201, base)[0] is True
        # Fullwidth lookalikes fold to ASCII under NFKC.
        allowed, reason = detector.check_message(1, 201, "ｂｕｙ ｃｒｙｐｔｏ ｎｏｗ")
        assert allowed is False
        assert reason == "Duplicate message detected"

    def test_duplicate_ignores_zero_width_variants(self, db):
        detector = _frozen_detector(db)
        base = "buy crypto now"
        for _ in range(DMSpamDetector.DEFAULT_DUPLICATE_THRESHOLD):
            assert detector.check_message(1, 202, base)[0] is True
        allowed, reason = detector.check_message(1, 202, "buy \u200bcrypto \u200dnow")
        assert allowed is False
        assert reason == "Duplicate message detected"

    def test_pattern_exact_match_ignores_padding_and_case(self, db):
        detector = _frozen_detector(db)
        detector.set_filter(
            user_id=203, filter_type="pattern", pattern="spam", action="block"
        )
        allowed, reason = detector.check_message(1, 203, "  SPAM  ")
        assert allowed is False
        assert reason is not None

    def test_pattern_filter_matches_fullwidth_variant(self, db):
        detector = _frozen_detector(db)
        detector.set_filter(
            user_id=204, filter_type="pattern", pattern="*spam*", action="block"
        )
        allowed, reason = detector.check_message(1, 204, "ｓｐａｍ here")
        assert allowed is False
        assert reason is not None

    def test_zero_width_invite_marker_still_counts(self, db):
        detector = _frozen_detector(db)
        content = "discord\u200b.gg/aaa discord\u200c.gg/bbb discord\u200d.gg/ccc"
        allowed, reason = detector.check_message(1, 205, content)
        assert allowed is False
        assert reason == "Message flagged as potential spam"


class TestHeuristicThresholdsM11:
    def test_single_invite_alone_allowed(self, db):
        # Contract pin: a single shared invite is legitimate traffic.
        detector = _frozen_detector(db)
        assert detector.check_message(1, 210, "join us on discord.gg/friends") == (
            True,
            None,
        )

    def test_single_url_alone_allowed(self, db):
        # A lone benign link must never be spam.
        detector = _frozen_detector(db)
        assert detector.check_message(
            1, 211, "check this out https://example.com/article"
        ) == (True, None)

    def test_three_urls_alone_still_allowed(self, db):
        # Boundary pin: moderate link sharing stays under threshold.
        detector = _frozen_detector(db)
        content = "http://one.example.com/x http://two.example.com/y http://t.example/z"
        assert detector.check_message(1, 212, content) == (True, None)

    def test_bulk_urls_alone_blocked(self, db):
        detector = _frozen_detector(db)
        content = " ".join(f"http://spam{i}.example.com/x" for i in range(6))
        allowed, reason = detector.check_message(1, 213, content)
        assert allowed is False
        assert reason == "Message flagged as potential spam"

    def test_five_mentions_alone_still_allowed(self, db):
        # Boundary pin: the mention tier below the bulk cutoff passes.
        detector = _frozen_detector(db)
        content = " ".join(f"<@{i}>" for i in range(300, 305))
        assert detector.check_message(1, 214, content) == (True, None)

    def test_mass_mentions_alone_blocked(self, db):
        detector = _frozen_detector(db)
        content = " ".join(f"<@{i}>" for i in range(400, 410))
        allowed, reason = detector.check_message(1, 215, content)
        assert allowed is False
        assert reason == "Message flagged as potential spam"

    def test_invite_plus_url_combo_blocked(self, db):
        detector = _frozen_detector(db)
        content = "https://a.example.com https://b.example.com join discord.gg/xyz"
        allowed, reason = detector.check_message(1, 216, content)
        assert allowed is False
        assert reason == "Message flagged as potential spam"


class TestFilterUpsertM12:
    def test_scope_spelling_variants_do_not_duplicate(self, db):
        detector = DMSpamDetector(db)
        detector.set_filter(user_id=220, filter_type="Rate", threshold=5)
        detector.set_filter(user_id=220, filter_type=" rate ", threshold=9)
        detector.set_filter(user_id=220, filter_type="RATE", threshold=7)
        rows = detector.get_filters(220)
        assert len(rows) == 1
        assert rows[0]["filter_type"] == "rate"
        assert rows[0]["threshold"] == 7

    def test_uniqueness_guards_installed(self, db):
        detector = DMSpamDetector(db)
        detector.set_filter(user_id=221, threshold=5)
        try:
            index_rows = db.fetch_all(
                "SELECT name FROM sqlite_master WHERE type = 'index' "
                "AND name LIKE 'uq_dm_spam_filters_%'"
            )
            names = {
                (row["name"] if isinstance(row, dict) else row[0])
                for row in (index_rows or [])
            }
        except Exception:
            pytest.skip("uniqueness guard not introspectable on this backend")
        assert "uq_dm_spam_filters_global" in names
        assert "uq_dm_spam_filters_targeted" in names

    def test_repeated_upsert_never_duplicates_either_scope(self, db):
        detector = DMSpamDetector(db)
        for threshold in (5, 9, 9):
            detector.set_filter(user_id=222, threshold=threshold)
        for threshold in (3, 8, 8):
            detector.set_filter(user_id=222, threshold=threshold, target_user_id=42)
        rows = detector.get_filters(222)
        assert len(rows) == 2
        by_scope = {r.get("target_user_id"): r for r in rows}
        assert by_scope[None]["threshold"] == 9
        assert by_scope[42]["threshold"] == 8
