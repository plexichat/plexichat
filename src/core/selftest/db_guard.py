"""Self-test error sanitization utility."""

from typing import Any


def sanitize_error(value: Any, limit: int = 200) -> str:
    """Sanitize raw exception/error strings before storing shared state.

    Error details end up in shared result state that can be surfaced by
    status endpoints, so they are reduced to a single-line, truncated
    summary (exception type + message) with control characters removed.
    """
    if isinstance(value, BaseException):
        text = f"{type(value).__name__}: {value}"
    else:
        try:
            text = str(value)
        except Exception:
            return "<unprintable error>"
    text = " ".join(text.split())
    if len(text) > limit:
        text = text[:limit] + "...[truncated]"
    return text
