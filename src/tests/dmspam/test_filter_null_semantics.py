"""Regression tests for dm-spam filter NULL semantics (L2/L3).

L2: remove_filter must return True only when a row was actually deleted
    (DELETE rowcount > 0); deleting a non-existent id returns False.

L3: set_filter must do a transactional upsert with proper IS NULL
    semantics so global (target_user_id IS NULL) and targeted
    (target_user_id = ?) scopes are never conflated and repeated
    set_filter calls never create duplicate rows.

The implementation lives in src/core/antispam/detector.py
(DMSpamDetector); src/core/dmspam/ holds the schema for these tables.
"""

from __future__ import annotations

import pytest

from src.core.antispam.detector import DMSpamDetector

pytestmark = pytest.mark.integration


def _detector(db):
    return DMSpamDetector(db)


def test_remove_filter_nonexistent_id_returns_false(db):
    detector = _detector(db)
    detector.set_filter(user_id=11, threshold=5)
    assert detector.remove_filter(11, 999_999) is False
    # The real filter is untouched.
    assert len(detector.get_filters(11)) == 1


def test_remove_filter_existing_returns_true(db):
    detector = _detector(db)
    detector.set_filter(user_id=12, threshold=5)
    rows = detector.get_filters(12)
    assert len(rows) == 1
    assert detector.remove_filter(12, rows[0]["id"]) is True
    assert detector.get_filters(12) == []
    # Second delete of the same id finds nothing.
    assert detector.remove_filter(12, rows[0]["id"]) is False


def test_set_global_then_targeted_yields_two_scoped_rows(db):
    detector = _detector(db)
    detector.set_filter(
        user_id=13, filter_type="pattern", pattern="*spam*", action="block"
    )
    detector.set_filter(
        user_id=13,
        filter_type="pattern",
        pattern="*spam*",
        action="block",
        target_user_id=7,
    )
    rows = detector.get_filters(13)
    assert len(rows) == 2
    scopes = sorted(
        (r.get("target_user_id") for r in rows), key=lambda v: (v is not None, v)
    )
    assert scopes == [None, 7]


def test_repeated_set_filter_upsert_does_not_duplicate(db):
    detector = _detector(db)
    # Global scope repeated.
    for threshold in (5, 9, 9):
        detector.set_filter(user_id=14, threshold=threshold, window_seconds=60)
    rows = detector.get_filters(14)
    assert len(rows) == 1
    assert rows[0]["target_user_id"] is None
    assert rows[0]["threshold"] == 9

    # Targeted scope repeated.
    for threshold in (3, 8, 8):
        detector.set_filter(
            user_id=14, threshold=threshold, window_seconds=60, target_user_id=42
        )
    rows = detector.get_filters(14)
    assert len(rows) == 2
    targeted = [r for r in rows if r.get("target_user_id") == 42]
    assert len(targeted) == 1
    assert targeted[0]["threshold"] == 8

    # Updating the global scope must not touch the targeted row.
    detector.set_filter(user_id=14, threshold=1, window_seconds=60)
    rows = detector.get_filters(14)
    assert len(rows) == 2
    by_scope = {r.get("target_user_id"): r for r in rows}
    assert by_scope[None]["threshold"] == 1
    assert by_scope[42]["threshold"] == 8
