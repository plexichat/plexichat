"""Regression: log/telemetry text must not carry executable control sequences.

Untrusted values that reach a log line or an admin log viewer can embed ANSI
escape sequences or raw control characters and repaint, clear or forge entries.
``scrub_control_characters`` is the shared presentation guard; these tests pin
it both directly and through the two sinks that consume it.
"""

from __future__ import annotations

import logging

import pytest

from src.core.admin import logs as admin_logs
from src.core.telemetry.scrubbing import scrub_control_characters


@pytest.fixture(autouse=True)
def _clean_index():
    admin_logs.clear_log_filter_index()
    yield
    admin_logs.clear_log_filter_index()


def test_scrub_control_characters_strips_ansi_escapes():
    text = "\x1b[31mERROR\x1b[0m \x1b[2J\x1b[H done"
    cleaned = scrub_control_characters(text)
    assert "\x1b" not in cleaned
    assert "ERROR" in cleaned and "done" in cleaned


def test_scrub_control_characters_renders_controls_visibly():
    assert scrub_control_characters("a\r\nb\x00c\x7fd") == ("a\\x0d\\x0ab\\x00c\\x7fd")
    assert scrub_control_characters(None) == ""


def test_admin_log_viewer_neutralises_control_chars(tmp_path, monkeypatch):
    line = "2026-11-16 12:00:00,000 - INFO - user said \x1b[2J\x1b[H hi\r there"
    (tmp_path / "app.log").write_text(line + "\n")
    monkeypatch.setattr(admin_logs, "get_log_dir", lambda: tmp_path)

    result = admin_logs.read_log_lines("app.log", limit=10)
    assert result["lines"], "the line should still be returned, just sanitised"
    served = result["lines"][0]
    for value in (served["message"], served["raw"]):
        assert "\x1b" not in value
        assert "\r" not in value
        assert "\x00" not in value
    assert "user said" in served["message"]


def test_telemetry_dropped_sample_log_is_sanitized(caplog):
    from src.core import telemetry

    class _StubDB:
        def execute_many(self, *a, **k):
            raise AssertionError("no valid rows should be inserted")

        def execute(self, *a, **k):
            return None

        def fetch_one(self, *a, **k):
            return None

    class _FailingEndpoint:
        """Valid for the size pre-pass; raises a raw-newline error in the loop."""

        def __init__(self):
            self.calls = 0

        def __str__(self):
            self.calls += 1
            if self.calls > 1:
                raise ValueError("bad endpoint:\nFORGED - ERROR - pwned")
            return "/x"

    telemetry.setup(_StubDB())
    with caplog.at_level(logging.DEBUG, logger="src.core.telemetry"):
        accepted = telemetry.submit_response_times(
            [{"endpoint": _FailingEndpoint(), "method": "GET"}]
        )
    assert accepted == 0
    rendered = "\n".join(rec.getMessage() for rec in caplog.records)
    # The hostile newline carried by the exception must not forge a second log
    # line; it is rendered as a visible escape instead.
    assert "bad endpoint:\\x0aFORGED" in rendered
    assert "bad endpoint:\nFORGED" not in rendered
