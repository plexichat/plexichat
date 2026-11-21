"""Regression: violating user text written to the automod audit rows must not
carry control characters that forge or disguise audit entries.

``AuditHandler.handle_violation`` clips a rule's ``matched_content`` -- text
quoted from the *attacking* user's message -- with ``clip_text`` and stores it
in ``automod_violations.matched_content`` and ``automod_audit.reason``.  A local
C0+DEL-only regex left U+0085 (NEL), the Unicode LINE/PARAGRAPH SEPARATORs and
the bidi/invisible format controls in that supposedly log-safe value, so a
single crafted message could split an audit entry into extra lines or render as
a different string than the one that was recorded.  ``clip_text`` now reuses
the shared control-character primitive, which removes the whole forbidden set.
"""

import pytest

from src.core.automod.limits import MAX_REASON_LENGTH, clip_text
from src.core.security.hardening.text import CONTROL_CHARACTERS

# Characters the old local C0+DEL-only regex left in an "audit-safe" value.
FORGING_CONTROLS = [
    "\x85",  # NEL -- a line break for many log shippers / decoders
    "\u2028",  # LINE SEPARATOR
    "\u2029",  # PARAGRAPH SEPARATOR
    "\u061c",  # ARABIC LETTER MARK
    "\u200e",  # LEFT-TO-RIGHT MARK
    "\u200f",  # RIGHT-TO-LEFT MARK
    "\u202e",  # RIGHT-TO-LEFT OVERRIDE
    "\u2066",  # LEFT-TO-RIGHT ISOLATE
    "\ufeff",  # BOM
]


@pytest.mark.parametrize("control", FORGING_CONTROLS)
def test_clip_text_removes_unicode_line_break_and_bidi_controls(control):
    out = clip_text(f"badword{control}INJECTED AUDIT LINE", MAX_REASON_LENGTH)
    assert control not in out
    assert out == "badwordINJECTED AUDIT LINE"


def test_clip_text_output_has_no_forbidden_control_characters():
    payload = "badword\u2028INJECTED\u2029NEL\x85RLO\u202eEND"
    out = clip_text(payload, MAX_REASON_LENGTH)
    assert CONTROL_CHARACTERS.search(out) is None


def test_clip_text_still_removes_c0_and_del_controls():
    out = clip_text("a\r\nb\x00c\x7fd", MAX_REASON_LENGTH)
    assert out == "abcd"


def test_clip_text_still_bounds_length():
    out = clip_text("a" * 2000 + "\u2028b", MAX_REASON_LENGTH)
    assert len(out) == MAX_REASON_LENGTH
    assert CONTROL_CHARACTERS.search(out) is None


def test_clip_text_clean_value_unchanged():
    assert (
        clip_text("obvious spam keyword", MAX_REASON_LENGTH) == "obvious spam keyword"
    )
