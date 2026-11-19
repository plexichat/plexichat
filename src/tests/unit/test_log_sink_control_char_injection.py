"""Regression: the on-disk log sink must not emit executable control sequences.

A caller-controlled value that reaches a log line -- most directly the
percent-decoded URL path in the access-log line written by ``LoggingMiddleware``
-- can carry a raw newline/carriage return or an ANSI CSI/OSC sequence and
forge, clear or repaint entries in the log file, a terminal or a viewer
(CWE-117). The admin viewer and telemetry apply the shared
``scrub_control_characters`` guard at presentation time, but that leaves the
file written by ``SanitizingFilter`` forgeable for anything that reads it
directly (SIEM shippers, support bundles, plain ``tail``). These tests pin the
guard at the actual sink.
"""

from __future__ import annotations

import logging

from src.utils.common_utils.utils.logger.core import SanitizingFilter
from src.utils.common_utils.utils.logger.sanitizer import sanitize_log_message


def _filtered(message: str, *args: object) -> str:
    record = logging.LogRecord(
        "AppLogger", logging.INFO, __file__, 1, message, args, None
    )
    SanitizingFilter().filter(record)
    return record.getMessage()


def test_sanitize_log_message_neutralises_crlf():
    line = "GET /ok\r\n2026-11-19 00:00:00,000 - ERROR - FORGED ADMIN ACTION - 10.0.0.1"
    cleaned = sanitize_log_message(line)
    assert "\r" not in cleaned and "\n" not in cleaned
    assert "\\x0d\\x0a" in cleaned
    # Forensics: the original text is still readable, just non-executable.
    assert "FORGED ADMIN ACTION" in cleaned


def test_sanitize_log_message_strips_ansi_and_c1_controls():
    hostile = "\x1b[2J\x1b[H ok\u009b31mFORGED\u009b0m nul\x00 del\x7f nel\u0085"
    cleaned = sanitize_log_message(hostile)
    for ch in ("\x1b", "\u009b", "\u009c", "\u009d", "\x00", "\x7f", "\u0085"):
        assert ch not in cleaned
    assert "ok" in cleaned and "FORGED" in cleaned
    assert "\\x85" in cleaned


def test_sink_filter_scrubs_lazy_access_log_args():
    """The exact access-log pattern: hostile path as a late-bound %-arg."""
    hostile_path = "/api/v1/x%0D%0AFORGED - ERROR - pwned".replace("%0D", "\r").replace(
        "%0A", "\n"
    )
    rendered = _filtered("%s - 200 - 0.10ms - 10.0.0.1", hostile_path)
    assert "\r" not in rendered and "\n" not in rendered
    assert "\\x0d\\x0a" in rendered
    assert "FORGED" in rendered


def test_sink_filter_scrubs_exception_text():
    record = logging.LogRecord(
        "AppLogger", logging.ERROR, __file__, 1, "boom", (), None
    )
    try:
        raise ValueError("bad input:\nFORGED - ERROR - pwned")
    except ValueError:
        import sys

        record.exc_info = sys.exc_info()
    SanitizingFilter().filter(record)
    assert record.exc_text is not None
    assert "\nFORGED" not in record.exc_text
    assert "bad input:\\x0aFORGED" in record.exc_text


def test_control_scrub_does_not_weaken_secret_redaction():
    # Sanitising controls must not reopen the secret-masking passes (CWE-532).
    msg = "Authorization: Bearer abc.def.ghi\r\napi_key=xyz987"
    cleaned = sanitize_log_message(msg)
    assert "\r" not in cleaned and "\n" not in cleaned
    assert "abc.def.ghi" not in cleaned
    assert "xyz987" not in cleaned
