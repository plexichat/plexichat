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
        "private_key",
        "credential",
        "bypass_secret",
    }
)
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}")
_KEY_VALUE = re.compile(
    r"(?i)\b(password|token|secret|api[_-]?key|authorization)\b\s*[:=]\s*\S+"
)


def _is_secret_key(key: str) -> bool:
    lowered = key.lower()
    return lowered in _SECRET_KEYS or any(
        token in lowered for token in ("password", "token", "secret", "api_key")
    )


def redact_text(text: str) -> str:
    """Redact credential-looking substrings from free text."""
    text = _BEARER.sub(f"Bearer {REDACTED}", text)
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
    text = str(value or "")
    text = _CONTROL.sub("", text)
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
        safe_key = _CONTROL.sub("", key)[:128] or "field"
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
