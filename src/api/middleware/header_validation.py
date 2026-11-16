"""Validation for header values that middleware copies from untrusted input.

An HTTP header is a syntax of its own: a ``\\r\\n`` (or a bare CR/LF, or DEL)
lets the value terminate the header block and inject a new one. Middleware
that reflects a request id, a client-supplied path or an upstream value into a
response header must validate it first. ``reject_control_characters`` from the
hardening package is the single source of truth for the control-character
rule; this module adds the length and name rules specific to headers.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, List, Tuple

from src.core.security.hardening import reject_control_characters

MAX_HEADER_NAME_LENGTH = 128
MAX_HEADER_VALUE_LENGTH = 1024
_HEADER_TOKEN = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")

Header = Tuple[bytes, bytes]


class HeaderValidationError(ValueError):
    """A header name or value violated the response-header invariants."""


def validate_header_name(name: Any, *, field: str = "header name") -> str:
    """Return a valid HTTP field name or raise ``HeaderValidationError``."""
    if not isinstance(name, str) or not name:
        raise HeaderValidationError(f"{field} must be a non-empty string")
    if len(name) > MAX_HEADER_NAME_LENGTH:
        raise HeaderValidationError(f"{field} is too long")
    if not _HEADER_TOKEN.match(name):
        raise HeaderValidationError(f"{field} contains invalid characters")
    return name


def validate_header_value(
    value: Any,
    *,
    field: str = "header value",
    max_length: int = MAX_HEADER_VALUE_LENGTH,
) -> str:
    """Return a header-safe string or raise ``HeaderValidationError``.

    Any C0 control (including CR and LF) or DEL is refused -- those are the
    header-splitting characters. The value is also bounded so a hostile
    upstream cannot make us emit a multi-megabyte header line.
    """
    if (
        isinstance(max_length, bool)
        or not isinstance(max_length, int)
        or max_length < 1
    ):
        raise HeaderValidationError("max_length must be a positive integer")
    if value is None:
        raise HeaderValidationError(f"{field} must not be null")
    try:
        text = reject_control_characters(value, field=field)
    except ValueError as exc:
        raise HeaderValidationError(str(exc)) from exc
    if len(text) > max_length:
        raise HeaderValidationError(f"{field} is too long")
    return text


def safe_header_value(
    value: Any,
    *,
    field: str = "header value",
    max_length: int = MAX_HEADER_VALUE_LENGTH,
    fallback: str = "",
) -> str:
    """Return a header-safe value, degrading to ``fallback`` when invalid.

    Use where a cosmetic value (a trace id, a debug label) must never turn a
    response into a 500 just because an upstream sent a stray control byte.
    """
    try:
        return validate_header_value(value, field=field, max_length=max_length)
    except HeaderValidationError:
        clean = _CONTROL.sub("", str(value or ""))[:max_length]
        return clean if clean else fallback


def validated_headers(headers: Iterable[Header]) -> List[Header]:
    """Validate a collection of ``(name, value)`` pairs, returning bytes."""
    result: List[Header] = []
    for name, value in headers:
        safe_name = validate_header_name(
            name.decode("latin-1") if isinstance(name, bytes) else name
        )
        raw_value = value.decode("latin-1") if isinstance(value, bytes) else value
        safe_value = validate_header_value(raw_value, field=f"header {safe_name}")
        result.append((safe_name.encode("latin-1"), safe_value.encode("latin-1")))
    return result


__all__ = [
    "MAX_HEADER_NAME_LENGTH",
    "MAX_HEADER_VALUE_LENGTH",
    "HeaderValidationError",
    "safe_header_value",
    "validate_header_name",
    "validate_header_value",
    "validated_headers",
]
