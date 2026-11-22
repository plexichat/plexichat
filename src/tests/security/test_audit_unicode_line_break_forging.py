"""Regression: audit/log fields must not carry Unicode line-break forgeries.

``bound_field``/``safe_log_line``/``sanitise_audit_entry`` promise log-safe
output, but a local C0+DEL-only regex let NEL (U+0085), the Unicode LINE and
PARAGRAPH SEPARATORs (U+2028/U+2029) and the bidi format controls through. Log
shippers, terminals and proxies treat those characters as line breaks or
invisible direction changes, so a single request value could forge extra
audit lines or render as a different string than the one that was recorded --
even though the shared hardening primitive rejects the very same set.

The fix reuses ``hardening.text.strip_control_characters`` everywhere. These
tests pin that contract and also check that redaction got strictly stronger,
never weaker: stripping the separators *before* the credential regexes means a
secret joined by one is now redacted in full instead of leaking its tail.
"""

import pytest

from src.core.security.audit import (
    REDACTED,
    bound_field,
    safe_log_line,
    sanitise_audit_entry,
)
from src.core.security.hardening.text import CONTROL_CHARACTERS

# Characters the old local regex left in a supposedly log-safe field.
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
def test_bound_field_removes_unicode_line_break_controls(control):
    out = bound_field(f"before{control}after")
    assert control not in out
    assert out == "beforeafter"


@pytest.mark.parametrize("control", FORGING_CONTROLS)
def test_safe_log_line_is_single_line(control):
    line = safe_log_line("event", f"value{control}tail")
    assert control not in line
    assert line == "event valuetail"


@pytest.mark.parametrize("control", FORGING_CONTROLS)
def test_audit_entry_key_controls_are_removed(control):
    record = sanitise_audit_entry({f"field{control}name": "value"})
    key = next(iter(record))
    assert control not in key
    assert CONTROL_CHARACTERS.search(key) is None


def test_unicode_separator_can_no_longer_split_a_redacted_secret():
    # A U+2028 between the two halves used to make ``\S+`` stop at the
    # separator, leaving the second half of the token in the log. Stripping
    # control characters first joins the token so the whole value is redacted.
    out = bound_field("token=abcdef\u2028ghijkl")
    assert "abcdefghijkl" not in out
    assert "ghijkl" not in out
    assert REDACTED in out


def test_c0_controls_still_removed_no_weakening():
    out = bound_field("a\r\nb\x00c\x7fd")
    assert "\r" not in out and "\n" not in out
    assert "\x00" not in out and "\x7f" not in out
    assert out == "abcd"


def test_clean_values_and_redaction_unchanged():
    assert bound_field("user logged in") == "user logged in"
    record = sanitise_audit_entry(
        {"session_cookie": "abcdef0123456789", "user": "alice"}
    )
    assert record["session_cookie"] == REDACTED
    assert record["user"] == "alice"
