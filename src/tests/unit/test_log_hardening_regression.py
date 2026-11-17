"""Regression: log viewers and audit sinks must survive hostile log text.

Two bypasses of the shared log guards are pinned here:

* C1 controls (U+0080-U+009F) include their own CSI/OSC/ST/NEL and are decoded
  like ESC-based sequences by terminals and browser viewers. A filter that only
  looks for ESC (0x1B) lets them through and a low-privileged user whose input
  reached a log can repaint or forge entries.
* camelCase/PascalCase secret keys (``clientSecret``, ``accessToken`` ...) were
  tokenised as a single opaque word and escaped redaction by name, even though
  their snake_case spellings were caught. Quoted JSON keys embedded after a
  message prefix were missed for the same reason.
"""

from __future__ import annotations

import pytest

from src.core.admin import logs as admin_logs
from src.core.telemetry.scrubbing import (
    REDACTED,
    redact_secrets,
    scrub_control_characters,
)


@pytest.fixture(autouse=True)
def _clean_index():
    admin_logs.clear_log_filter_index()
    yield
    admin_logs.clear_log_filter_index()


def test_c1_control_characters_are_neutralised():
    # U+009B (CSI), U+009D (OSC), U+009C (ST) and U+0085 (NEL) are all C1
    # controls that viewers interpret as formatting/line breaks.
    hostile = (
        "ok\u009b31mFORGED\u009b0m"  # C1 SGR colour
        "\u009d0;FORGED TITLE\u009c"  # C1 OSC title terminated by C1 ST
        "line1\u0085FORGED - ERROR"  # C1 NEL forges a new log line
    )
    cleaned = scrub_control_characters(hostile)
    for c1 in ("\u009b", "\u009c", "\u009d", "\u0085"):
        assert c1 not in cleaned
    assert "\\x9b" in cleaned and "\\x85" in cleaned
    assert "FORGED" in cleaned  # text is preserved for forensics


def test_c1_controls_do_not_hide_esc_sequences():
    # Neutralising C1 must not reintroduce an executable ESC-based sequence.
    cleaned = scrub_control_characters("\u009b\x1b[2J\x1b[H")
    assert "\x1b" not in cleaned


@pytest.mark.parametrize(
    "key",
    [
        "clientSecret",
        "accessToken",
        "refreshToken",
        "passwordHash",
        "apiKey",
        "idToken",
        "AUTH_TOKEN",
        "APIKey",
    ],
)
def test_camelcase_sensitive_keys_are_redacted(key):
    secret = "camelcase-secret-value"
    assert redact_secrets({key: secret})[key] == REDACTED


def test_non_sensitive_keys_are_still_preserved():
    # Splitting camelCase must not turn harmless keys into sensitive ones.
    assert redact_secrets({"author": "alice", "monkey": "curious"}) == {
        "author": "alice",
        "monkey": "curious",
    }


def test_inline_camelcase_and_quoted_json_keys_are_redacted():
    samples = [
        "clientSecret=camel-secret-1",
        "refreshToken: camel-secret-2",
        'payload={"password": "quoted-secret", "accessToken": "quoted-token"}',
        "prefix={'secret': 'single-quoted-secret'}",
    ]
    for text in samples:
        cleaned = redact_secrets(text)
        assert "camel-secret" not in cleaned
        assert "quoted-secret" not in cleaned
        assert "quoted-token" not in cleaned
        assert "single-quoted-secret" not in cleaned


def test_admin_log_viewer_scrubs_c1_and_camelcase_secrets(tmp_path, monkeypatch):
    line = (
        "2026-11-17 12:00:00,000 - INFO - "
        'result={"clientSecret": "VIEWER-SECRET"} ok\u009b31mFORGED\u009b0m'
    )
    (tmp_path / "app.log").write_text(line + "\n", encoding="utf-8")
    monkeypatch.setattr(admin_logs, "get_log_dir", lambda: tmp_path)

    result = admin_logs.read_log_lines("app.log", limit=10)
    assert result["lines"], "the line should still be returned, just sanitised"
    served = result["lines"][0]
    for value in (served["message"], served["raw"]):
        assert "VIEWER-SECRET" not in value
        assert "\u009b" not in value
        assert "\x1b" not in value
        assert "\\x9b" in value
    assert "result=" in served["message"]
