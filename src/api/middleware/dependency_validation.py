"""Bounded parsing for dependency-resolved ids and path/query parameters.

FastAPI path/query parameters are strings until a dependency converts them.
``int(raw)`` happily accepts a 40-digit id, a negative page, or a limit of
``10**9``; each then flows into a lookup, an ``OFFSET`` or a response-size
allocation. These helpers convert such values through the hardening rules so
a dependency yields a value inside a real range or rejects the request.
"""

from __future__ import annotations

from typing import Any, Optional

from src.core.security.hardening import HardeningViolation, bounded_int, parse_id

MAX_PAGE = 100_000
MAX_LIMIT = 500
DEFAULT_LIMIT = 50
MAX_PARAM_TEXT = 512


def bounded_id(value: Any, field: str = "id") -> int:
    """Parse a snowflake-style identifier from a path/query parameter."""
    # ``int(1.5)`` truncates to 1; an id is never a fractional number, so
    # refuse it rather than silently resolving a different resource.
    if isinstance(value, float) and not value.is_integer():
        raise HardeningViolation(f"{field} must be an integer")
    return parse_id(value, field)


def optional_id(value: Any, field: str = "id") -> Optional[int]:
    """Parse an optional identifier; ``None``/blank means "not supplied"."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    return bounded_id(value, field)


def bounded_page(value: Any, default: int = 1) -> int:
    """Coerce a 1-based page number into ``[1, MAX_PAGE]``."""
    return bounded_int(value, "page", minimum=1, maximum=MAX_PAGE, default=default)


def bounded_limit(
    value: Any, default: int = DEFAULT_LIMIT, maximum: int = MAX_LIMIT
) -> int:
    """Coerce a page size into ``[1, maximum]``."""
    return bounded_int(value, "limit", minimum=1, maximum=maximum, default=default)


def parse_param_int(
    value: Any,
    field: str,
    *,
    minimum: int,
    maximum: int,
    default: Optional[int] = None,
) -> int:
    """Generic bounded integer query parameter."""
    return bounded_int(value, field, minimum=minimum, maximum=maximum, default=default)


def bounded_param_text(value: Any, field: str = "value") -> str:
    """Return a short, control-free text parameter.

    Reuses the hardening rule for control characters so a query parameter
    cannot smuggle a newline into a log line or a response header.
    """
    from src.core.security.hardening import reject_control_characters

    text = reject_control_characters(value, field=field)
    if len(text) > MAX_PARAM_TEXT:
        raise HardeningViolation(f"{field} is too long")
    return text


__all__ = [
    "DEFAULT_LIMIT",
    "MAX_LIMIT",
    "MAX_PAGE",
    "MAX_PARAM_TEXT",
    "bounded_id",
    "bounded_limit",
    "bounded_page",
    "bounded_param_text",
    "optional_id",
    "parse_param_int",
]
