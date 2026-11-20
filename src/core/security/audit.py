"""Bounded, secret-safe construction of audit and log entries.

Audit records and log lines are written to disk and shipped to operators. Two
things must never reach them unbounded or in the clear:

* a field copied from a request (a display name, a path, an error string) can
  contain control characters that forge extra log lines, or be megabytes long;
* a credential (token, password, cookie, private key) can be logged verbatim
  by a process that logs "the request that failed".

This module provides one place to bound every field, drop control characters
and redact known secret-bearing keys and patterns before a record is emitted.
"""

from __future__ import annotations

import re
from typing import Any, Dict

from src.core.security.hardening import bounded_int

# Use the shared control-character rule, not a local C0+DEL-only regex. NEL
# (U+0085), the Unicode LINE/PARAGRAPH SEPARATORs (U+2028/U+2029) and the bidi
# format controls are all treated as line breaks or invisible direction changes
# by log shippers, terminals and proxies. Leaving any of them in a "log-safe"
# field lets a request value forge extra log/audit lines or render as a different
# string than the one that was recorded. The shared primitive removes the whole
# set (see hardening.text), so every consumer stays on the same side of that
# parser differential.
from src.core.security.hardening.text import strip_control_characters

MAX_FIELDS = 50
MAX_VALUE_LENGTH = 512
REDACTED = "[REDACTED]"

# Keys whose *values* are secrets regardless of where they appear in a record.
_SECRET_KEYS = frozenset(
    {
        "password",
        "password_hash",
        "token",
        "access_token",
        "refresh_token",
        "secret",
        "client_secret",
        "api_key",
        "authorization",
        "cookie",
        "session",
        "session_id",
        "sessionid",
        "set-cookie",
        "x-api-key",
        "private_key",
        "credential",
        "bypass_secret",
    }
)
_BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}")
# HTTP Basic carries a base64 "user:password" credential in the token position.
# Without its own rule the generic key/value pattern below consumes only the
# word "Basic" (it stops at whitespace) and leaves the credential in the log.
_BASIC = re.compile(r"(?i)\bbasic\s+[A-Za-z0-9+/=]{8,}")
_KEY_VALUE = re.compile(
    r"(?i)\b(client_secret|private_key|password|passwd|authorization|set-cookie|"
    r"api[_-]?key|sessionid|session|cookie|token|secret|credential|sid)\b"
    r"\s*[:=]\s*(?:\"[^\"]*\"|'[^']*'|\S+)"
)
# Substring check as well as exact names: a field called "session_cookie" or
# "auth_cookie" holds a credential just as surely as one called "cookie".
_SECRET_KEY_PARTS = (
    "password",
    "token",
    "secret",
    "api_key",
    "cookie",
    "session",
    "credential",
    "bearer",
)


def _is_secret_key(key: str) -> bool:
    lowered = key.lower()
    return lowered in _SECRET_KEYS or any(part in lowered for part in _SECRET_KEY_PARTS)


def redact_text(text: str) -> str:
    """Redact credential-looking substrings from free text."""
    text = _BEARER.sub(f"Bearer {REDACTED}", text)
    text = _BASIC.sub(f"Basic {REDACTED}", text)
    text = _KEY_VALUE.sub(lambda m: f"{m.group(1)}={REDACTED}", text)
    return text


def bound_field(
    value: Any, field: str = "value", *, max_length: int = MAX_VALUE_LENGTH
) -> str:
    """Return a log-safe string: no control characters, bounded length, redacted."""
    if (
        isinstance(max_length, bool)
        or not isinstance(max_length, int)
        or max_length < 1
    ):
        raise ValueError("max_length must be a positive integer")
    text = strip_control_characters(value)
    # Strip BEFORE redacting: the credential patterns use ``\s``/``\S``, which
    # treat NEL and U+2028/U+2029 as whitespace. Removing those first keeps a
    # secret joined into one token so the pattern can redact all of it instead
    # of leaving the tail of a credential in the record.
    text = redact_text(text)
    if len(text) > max_length:
        text = text[:max_length] + "…[truncated]"
    return text


def sanitise_audit_entry(
    data: Dict[str, Any],
    *,
    max_fields: int = MAX_FIELDS,
    max_value_length: int = MAX_VALUE_LENGTH,
) -> Dict[str, str]:
    """Bound and redact every field of an audit record."""
    bounded_fields = bounded_int(max_fields, "max_fields", minimum=1, maximum=1000)
    result: Dict[str, str] = {}
    for key in sorted(str(k) for k in data):
        if len(result) >= bounded_fields:
            result["_truncated_fields"] = str(len(data) - bounded_fields)
            break
        safe_key = strip_control_characters(key)[:128] or "field"
        if _is_secret_key(safe_key):
            result[safe_key] = REDACTED
        else:
            result[safe_key] = bound_field(
                data[key], safe_key, max_length=max_value_length
            )
    return result


def safe_log_line(*parts: Any) -> str:
    """Build a single log line with no embedded newlines from arbitrary parts."""
    return " ".join(bound_field(part, max_length=MAX_VALUE_LENGTH) for part in parts)


__all__ = [
    "MAX_FIELDS",
    "MAX_VALUE_LENGTH",
    "REDACTED",
    "bound_field",
    "redact_text",
    "safe_log_line",
    "sanitise_audit_entry",
]
