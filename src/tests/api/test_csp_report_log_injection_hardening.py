"""Regression tests for the unauthenticated CSP report log sink.

``POST /telemetry/csp-report`` is public and writes an attacker-controlled JSON
document into the application log.  Before this hardening the sanitizer only
redacted keys ending in ``-uri`` plus ``referrer``, so a credential-bearing
``blocked-url`` query string was logged verbatim, and no string had its
C0/DEL control characters escaped (a newline in ``script-sample`` forges extra
log lines).
"""

import json

from src.api.routes.telemetry import (
    _MAX_CSP_URI_CHARS,
    _redact_uri,
    _sanitize_csp_report,
)


def _log_line(report):
    """The exact serialization the route hands to logger.info()."""
    return json.dumps(_sanitize_csp_report(report))


def test_blocked_url_query_and_fragment_are_redacted():
    report = {
        "blocked-url": "https://app.example.com/callback?code=SECRET&token=SECRET#frag",
        "blocked-uri": "https://cdn.example.com/a.js?api_key=SECRET",
        "document-uri": "https://app.example.com/page?session=SECRET",
        "referrer": "https://ref.example.com/?s=SECRET",
        "source-file": "https://app.example.com/app.js?sig=SECRET",
    }
    line = _log_line(report)
    assert "SECRET" not in line
    assert "frag" not in line
    clean = _sanitize_csp_report(report)
    assert clean["blocked-url"] == "https://app.example.com/callback"
    assert clean["blocked-uri"] == "https://cdn.example.com/a.js"
    assert clean["document-uri"] == "https://app.example.com/page"
    assert clean["referrer"] == "https://ref.example.com/"
    assert clean["source-file"] == "https://app.example.com/app.js"


def test_control_characters_cannot_forge_log_lines():
    report = {
        "script-sample": "console.log('x')\nINFO admin login ok\telated\r\nWARN root",
        "effective-directive": "script-src\x00-elem\x7f",
        "nested": [
            {"blocked-url": "https://x.example/a?token=SECRET\nNEXT ok"},
            "plain\x1b[31mred\x1b[0m",
        ],
    }
    line = _log_line(report)
    # json.dumps escapes embedded newlines, but the raw control bytes must be
    # gone from the sanitized structure itself so every log backend is safe.
    assert "\n" not in line
    assert "\r" not in line
    assert "\x00" not in line
    assert "\x7f" not in line
    assert "\x1b" not in line
    assert "\t" not in line
    assert "SECRET" not in line
    assert "NEXT ok" not in line
    clean = _sanitize_csp_report(report)
    assert "\\x0a" in clean["script-sample"]
    assert "\\x00" in clean["effective-directive"]
    assert "\\x7f" in clean["effective-directive"]


def test_string_values_are_still_bounded_at_every_depth():
    long = "a" * (_MAX_CSP_URI_CHARS + 100)
    clean = _sanitize_csp_report(
        {"blocked-url": f"https://x.example/{long}?token=SECRET", "items": [long]}
    )
    assert len(clean["blocked-url"]) == _MAX_CSP_URI_CHARS
    assert len(clean["items"][0]) == _MAX_CSP_URI_CHARS
    assert "SECRET" not in json.dumps(clean)


def test_non_url_fields_keep_their_value_except_for_controls():
    report = {"status-code": 403, "violated-directive": "script-src 'self'", "ok": True}
    assert _sanitize_csp_report(report) == report


def test_redact_uri_helper_strips_fragment():
    assert _redact_uri("https://x.example/p?q=1#f") == "https://x.example/p"
