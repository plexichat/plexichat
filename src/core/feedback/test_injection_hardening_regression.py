"""Regression tests for core feedback-boundary injection hardening.

``submit_feedback`` is a public core helper as well as the storage backend of
the HTTP route. The route rejects control/bidi characters, bounds content and
category shape, and constrains the rating, but a direct caller could previously
persist arbitrary text that is later rendered verbatim in moderator UIs and log
lines. These tests pin the same constraints at the core boundary.
"""

from __future__ import annotations

import pytest

import src.core.feedback as feedback_mod

pytestmark = pytest.mark.integration


class _RecordingDB:
    """Capture inserts without needing a real database."""

    def __init__(self):
        self.inserts = []

    def execute(self, sql, params=None):
        self.inserts.append((sql, params))

        class _Cursor:
            rowcount = 1

        return _Cursor()

    def fetch_one(self, *args, **kwargs):
        return None


@pytest.fixture
def db():
    fake = _RecordingDB()
    feedback_mod.setup(fake)
    return fake


def _submit(db, **overrides):
    kwargs = {
        "user_id": 42,
        "content": "Please fix the voice feature",
        "category": "ui",
        "rating": 4,
    }
    kwargs.update(overrides)
    return feedback_mod.submit_feedback(**kwargs)


def test_valid_direct_submission_is_stored_unchanged(db):
    fid = _submit(db, content="Line one\nLine two\t(tab)")
    assert fid > 0
    assert len(db.inserts) == 1
    stored = db.inserts[0][1]
    assert stored[2] == "Line one\nLine two\t(tab)"
    assert stored[3] == "ui"
    assert stored[4] == 4


@pytest.mark.parametrize("bad", ["\x00", "\x1b[31m", "\r\n", "\x7f"])
def test_rejects_log_breaking_control_characters(db, bad):
    with pytest.raises(ValueError):
        _submit(db, content="bad" + bad + "content")
    assert db.inserts == []


@pytest.mark.parametrize("codepoint", [0x200B, 0x202E, 0x2066, 0xFEFF])
def test_rejects_bidi_and_invisible_characters(db, codepoint):
    with pytest.raises(ValueError):
        _submit(db, content="looks" + chr(codepoint) + "normal")
    assert db.inserts == []


def test_rejects_non_string_and_oversized_content(db):
    with pytest.raises(ValueError):
        _submit(db, content={"not": "text"})
    with pytest.raises(ValueError):
        _submit(db, content="x" * (feedback_mod.MAX_FEEDBACK_CONTENT_LENGTH + 1))
    assert db.inserts == []


@pytest.mark.parametrize(
    "category",
    ["<script>", "has space", "UI", "x" * 51, "line\nbreak", 123],
)
def test_rejects_injected_category_but_accepts_plain_labels(db, category):
    with pytest.raises(ValueError):
        _submit(db, category=category)
    assert db.inserts == []
    # A short lower-case label (the historical "ui" value included) stays valid.
    assert _submit(db, category="ui") > 0


@pytest.mark.parametrize("rating", [0, 6, -1, True, "5"])
def test_rejects_out_of_range_or_non_integer_rating(db, rating):
    with pytest.raises(ValueError):
        _submit(db, rating=rating)
    assert db.inserts == []
    assert _submit(db, rating=None) > 0
