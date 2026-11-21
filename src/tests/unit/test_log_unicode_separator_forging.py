"""Regression: log-text guards must neutralise Unicode line separators and bidi controls.

A percent-decoded URL path (or any caller-controlled value) reaches the on-disk
log through ``LoggingMiddleware``. The sink guard and the telemetry / admin-viewer
presentation guard both used a C0/DEL/C1-only regex, so U+2028 / U+2029 (newlines
for many log shippers, proxies and browser viewers) and the bidi / invisible
format controls were written verbatim. The request-schema boundary,
``hardening.text`` and ``core.security.audit`` all treat that same set as unsafe
-- a parser differential that let one logged request render as extra entries
downstream. This pins both guards to the shared set.
"""

from __future__ import annotations

import logging

import pytest

from src.core.security.hardening.text import CONTROL_CHARACTERS
from src.core.telemetry.scrubbing import scrub_control_characters
from src.utils.common_utils.utils.logger.core import SanitizingFilter
from src.utils.common_utils.utils.logger.sanitizer import sanitize_log_message

pytestmark = [pytest.mark.unit, pytest.mark.security]

FORGING_CONTROLS = [
    "\u2028",  # LINE SEPARATOR
    "\u2029",  # PARAGRAPH SEPARATOR
    "\u061c",  # ARABIC LETTER MARK
    "\u200e",  # LEFT-TO-RIGHT MARK
    "\u200f",  # RIGHT-TO-LEFT MARK
    "\u070f",  # SYRIAC ABBREVIATION MARK (strong RTL)
    "\u202a",  # LEFT-TO-RIGHT EMBEDDING
    "\u202e",  # RIGHT-TO-LEFT OVERRIDE
    "\u2066",  # LEFT-TO-RIGHT ISOLATE
    "\u2069",  # POP DIRECTIONAL ISOLATE
    "\u206f",  # NOMINAL DIGIT SHAPES
    "\u2060",  # WORD JOINER
    "\ufeff",  # BOM
]


def test_payloads_are_in_the_shared_unsafe_set():
    # Non-vacuous: these really are the characters every other boundary rejects.
    for control in FORGING_CONTROLS:
        assert CONTROL_CHARACTERS.search(control), repr(control)


@pytest.mark.parametrize("control", FORGING_CONTROLS)
def test_sink_sanitizer_escapes_unicode_separator(control):
    cleaned = sanitize_log_message(f"GET /ok{control}FORGED - 1.2.3.4")
    assert control not in cleaned
    assert f"\\u{ord(control):04x}" in cleaned or f"\\x{ord(control):02x}" in cleaned
    assert "FORGED" in cleaned  # forensic text preserved, just non-executable


@pytest.mark.parametrize("control", FORGING_CONTROLS)
def test_telemetry_presentation_guard_escapes_unicode_separator(control):
    cleaned = scrub_control_characters(f"value{control}tail")
    assert control not in cleaned
    assert f"\\u{ord(control):04x}" in cleaned or f"\\x{ord(control):02x}" in cleaned


def test_sink_filter_scrubs_lazy_access_log_path_arg():
    # Exact LoggingMiddleware shape: hostile path as a late-bound %-arg.
    hostile_path = "/api/v1/x\u2028ERROR - FORGED ADMIN ACTION - 10.0.0.1"
    record = logging.LogRecord(
        "AppLogger",
        logging.INFO,
        __file__,
        1,
        "%s - 200 - 0.10ms - 10.0.0.1",
        (hostile_path,),
        None,
    )
    SanitizingFilter().filter(record)
    rendered = record.getMessage()
    assert "\u2028" not in rendered
    assert "\\u2028" in rendered
    assert "FORGED ADMIN ACTION" in rendered


def test_c0_c1_and_ansi_still_scrubbed():
    cleaned = sanitize_log_message("\x1b[2J ok\r\n nul\x00 del\x7f nel\u0085")
    for ch in ("\x1b", "\r", "\n", "\x00", "\x7f", "\u0085"):
        assert ch not in cleaned
    assert "\\x0d\\x0a" in cleaned and "\\x85" in cleaned


def test_zero_width_joiner_still_allowed():
    value = "caf\u00e9 \U0001f468\u200d\U0001f469"
    assert sanitize_log_message(value) == value
    assert scrub_control_characters(value) == value
