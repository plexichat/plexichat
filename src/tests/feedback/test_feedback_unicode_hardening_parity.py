"""Feedback free-text must reject the full unsafe-Unicode set used elsewhere.

The feedback route and core storage boundary used a hand-maintained subset of
control/invisible code points. Unlike the project-wide ``CONTROL_CHARACTERS``
gate (which the report path already applies), that subset omitted C1 controls
including U+0085 NEL, the U+2028/U+2029 line/paragraph separators, the Arabic
letter mark, the Syriac abbreviation mark, the word joiner and the legacy bidi
format band. Those code points are invisible or render as a line break in
moderator UIs and log shippers, so feedback carrying them could spoof a
moderator-facing sentence or forge a log/UI line even though the same text is
rejected on the report path. A plain \n/\t allowance for multi-line feedback
must not reopen the gap.
"""

import pytest

from src.api.routes.feedback import _safe_feedback_content
from src.core.security.hardening.text import CONTROL_CHARACTERS
import src.core.feedback as core_feedback


# Every one of these is flagged by the project's shared gate but was accepted
# by both feedback validators before this fix.
GAP_CODEPOINTS = [
    0x85,  # C1 NEXT LINE - a line break for many log shippers
    0x2028,  # LINE SEPARATOR
    0x2029,  # PARAGRAPH SEPARATOR
    0x061C,  # ARABIC LETTER MARK (invisible bidi)
    0x070F,  # SYRIAC ABBREVIATION MARK (invisible bidi)
    0x2060,  # WORD JOINER (invisible)
    0x206A,  # INHIBIT SYMMETRIC SWAPPING (legacy directional format)
    0x206F,  # NOMINAL DIGIT SHAPES (legacy directional format)
]


def test_gap_codepoints_are_in_the_shared_standard():
    """Guard the premise: these are genuinely unsafe by the shared contract."""
    for cp in GAP_CODEPOINTS:
        assert CONTROL_CHARACTERS.search(chr(cp)), f"U+{cp:04X} not in shared gate"


@pytest.mark.parametrize("codepoint", GAP_CODEPOINTS)
def test_route_rejects_gap_codepoints(codepoint):
    with pytest.raises(ValueError):
        _safe_feedback_content("looks" + chr(codepoint) + "safe")


@pytest.mark.parametrize("codepoint", GAP_CODEPOINTS)
def test_core_validator_rejects_gap_codepoints(codepoint):
    with pytest.raises(ValueError):
        core_feedback._validated_content("looks" + chr(codepoint) + "safe")


def test_core_submission_rejects_gap_codepoint_before_any_db_write():
    """The storage boundary must fail closed without persisting the row."""

    class _RecordingDB:
        def __init__(self):
            self.inserts = []

        def execute(self, sql, params=None):
            self.inserts.append((sql, params))

            class _Cursor:
                rowcount = 1

            return _Cursor()

    db = _RecordingDB()
    core_feedback.setup(db)
    with pytest.raises(ValueError):
        core_feedback.submit_feedback(
            user_id=1, content="line one\u2028INFO forged moderator line"
        )
    assert db.inserts == []


def test_plain_multiline_feedback_still_accepted():
    """The \n/\t allowance that multi-line feedback relies on is preserved."""
    text = "line one\nline two\tindented"
    assert _safe_feedback_content(text) == text
    assert core_feedback._validated_content(text) == text
