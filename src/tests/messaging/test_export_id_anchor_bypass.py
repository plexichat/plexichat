"""Regression: export-id validation must anchor at the real end of the string.

``_is_valid_export_id`` guards every path that turns a request-supplied
``export_id`` into a record/file lookup. It used ``_EXPORT_ID_RE.match`` with
a ``$`` terminator. In Python ``$`` also matches *just before* a trailing
newline, so ``export_<entropy>\\n`` was accepted as a "server-shaped" id even
though the generator never emits one. The newline then survived into the
bookkeeping/filename path that the validator is supposed to keep traversal-safe.
"""

from __future__ import annotations

import re

import pytest

from src.core.messaging.services.export import (
    _EXPORT_ID_RE,
    TranscriptExportService,
)

# The pre-fix pattern, kept verbatim so the test documents the exact bypass
# instead of merely asserting today's behaviour.
_LEGACY_EXPORT_ID_RE = re.compile(r"^export_[A-Za-z0-9_-]{16,}$")
_VALID_ID = "export_" + "A" * 16
_TRAILING_NEWLINE_ID = _VALID_ID + "\n"


def test_legacy_match_anchor_premise():
    """A trailing newline bypasses ``match`` + ``$`` but not ``fullmatch``."""
    assert _LEGACY_EXPORT_ID_RE.match(_TRAILING_NEWLINE_ID) is not None
    assert _LEGACY_EXPORT_ID_RE.fullmatch(_TRAILING_NEWLINE_ID) is None


@pytest.mark.parametrize(
    "crafted",
    [
        _TRAILING_NEWLINE_ID,
        _VALID_ID + "\r",
        _VALID_ID + "\t",
        _VALID_ID + "\x00",
        _VALID_ID + "\x1f",
        _VALID_ID + "\u2028",
        _VALID_ID + "\nJUNK",
    ],
)
def test_trailing_control_char_is_rejected(crafted):
    assert TranscriptExportService._is_valid_export_id(crafted) is False


@pytest.mark.parametrize(
    "valid",
    [
        _VALID_ID,
        "export_" + "a" * 16,
        "export_" + "aZ09_-" * 3,
        "export_" + "x" * 256,
    ],
)
def test_legitimate_export_ids_still_valid(valid):
    assert TranscriptExportService._is_valid_export_id(valid) is True


@pytest.mark.parametrize(
    "invalid",
    [
        None,
        123,
        b"export_" + b"A" * 16,
        "export_short",
        "other_" + "A" * 16,
        "../../" + _VALID_ID,
        _VALID_ID + "/../etc/passwd",
    ],
)
def test_non_matching_values_rejected(invalid):
    assert TranscriptExportService._is_valid_export_id(invalid) is False


def test_pattern_uses_end_of_string_anchor():
    """Defence in depth: the pattern itself must not end in a bare ``$``."""
    assert r"\Z" in _EXPORT_ID_RE.pattern
    assert not _EXPORT_ID_RE.pattern.endswith("$")
