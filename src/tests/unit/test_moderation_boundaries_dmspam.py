"""DM-spam burst parameters and content are bounded."""

import pytest

from src.core.antispam.detector import (
    MAX_DM_CONTENT_LENGTH,
    MAX_FILTER_PATTERN_LENGTH,
    MAX_FILTER_THRESHOLD,
    MAX_FILTER_WINDOW_SECONDS,
    DMSpamDetector,
    _bounded_filter_int,
)


class FakeDB:
    def __init__(self, rowcount=0):
        self.rowcount = rowcount

    def execute(self, *args, **kwargs):
        return self

    def begin_transaction(self):
        pass

    def commit(self):
        pass

    def rollback(self):
        pass


def test_filter_int_bounds():
    assert _bounded_filter_int(10, "x", 1, 100, 5) == 10
    for value in (-1, 0, 101, 10**12, "abc", None):
        assert _bounded_filter_int(value, "x", 1, 100, 5) == 5


@pytest.mark.parametrize(
    "kwargs",
    [
        {"threshold": 0},
        {"threshold": MAX_FILTER_THRESHOLD + 1},
        {"window_seconds": 0},
        {"window_seconds": MAX_FILTER_WINDOW_SECONDS + 1},
        {"filter_type": "invalid"},
        {"action": "nuke"},
        {"pattern": "x" * (MAX_FILTER_PATTERN_LENGTH + 1)},
    ],
)
def test_set_filter_rejects_out_of_range(kwargs):
    detector = DMSpamDetector(FakeDB())
    with pytest.raises(ValueError):
        detector.set_filter(1, **kwargs)


def test_set_filter_accepts_valid_values():
    detector = DMSpamDetector(FakeDB(rowcount=1))
    result = detector.set_filter(
        1,
        filter_type="rate",
        action="block",
        threshold=MAX_FILTER_THRESHOLD,
        window_seconds=MAX_FILTER_WINDOW_SECONDS,
    )
    assert result["action"] == "block"


def test_long_content_is_truncated_before_matching():
    detector = DMSpamDetector(FakeDB())
    detector._get_user_filters = lambda recipient_id, sender_id: []
    detector._is_new_user = lambda sender_id: False
    from src.core.antispam import detector as module

    seen = {}
    original = module._canonical_text

    def capture(content):
        seen["length"] = len(content)
        return original(content)

    module._canonical_text = capture
    try:
        detector.check_message(1, 2, "a" * (MAX_DM_CONTENT_LENGTH + 500))
    finally:
        module._canonical_text = original
    assert seen["length"] == MAX_DM_CONTENT_LENGTH
