"""Regression: credentials embedded in free text must all be redacted.

The generic ``key=value`` redaction stopped at whitespace, so an HTTP Basic
header left its base64 ``user:password`` payload in the log. Cookie/session
values and non-canonical secret field names (``session_cookie``,
``auth_cookie``) were not covered at all. All of these end up in log lines via
``bound_field``/``safe_log_line`` and in audit records via
``sanitise_audit_entry``.
"""

from src.core.security.audit import (
    REDACTED,
    bound_field,
    redact_text,
    sanitise_audit_entry,
)


def test_basic_auth_credential_redacted():
    out = redact_text("Authorization: Basic dXNlcjpwYXNzd29yZA==")
    assert "dXNlcjpwYXNzd29yZA==" not in out
    assert REDACTED in out


def test_basic_auth_lowercase_redacted():
    out = redact_text("request header 'basic YWxpY2U6czNjcmV0' rejected")
    assert "YWxpY2U6czNjcmV0" not in out
    assert REDACTED in out


def test_cookie_value_redacted():
    out = redact_text("Cookie: session=abcdef0123456789")
    assert "abcdef0123456789" not in out
    assert REDACTED in out


def test_set_cookie_value_redacted():
    out = redact_text("Set-Cookie: sid=9f8e7d6c5b4a3210; Path=/")
    assert "9f8e7d6c5b4a3210" not in out
    assert REDACTED in out


def test_quoted_secret_value_redacted():
    out = redact_text('api_key="supersecretvalue with spaces"')
    assert "supersecretvalue" not in out
    assert REDACTED in out


def test_noncanonical_secret_key_redacted_in_audit_record():
    record = sanitise_audit_entry(
        {
            "session_cookie": "abcdef0123456789",
            "auth_cookie": "deadbeefcafe",
            "user": "alice",
        }
    )
    assert record["user"] == "alice"
    assert record["session_cookie"] == REDACTED
    assert record["auth_cookie"] == REDACTED
    assert "abcdef0123456789" not in repr(record)
    assert "deadbeefcafe" not in repr(record)


def test_bound_field_redacts_basic_credential():
    out = bound_field("Authorization: Basic dXNlcjpwYXNzd29yZA==")
    assert "dXNlcjpwYXNzd29yZA==" not in out


def test_clean_text_still_unchanged():
    assert redact_text("user logged in") == "user logged in"
