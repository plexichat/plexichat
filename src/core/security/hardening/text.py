"""Rules for text that is copied into a header, log line or index as-is.

C0 controls (and DEL) are the characters that break out of an HTTP header or
forge a log line; they are never legitimate in a checksum, request id or short
machine token.
"""

from __future__ import annotations

import re
from typing import Any

from .errors import HardeningViolation

CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")


def reject_control_characters(value: Any, field: str = "value") -> str:
    """Return ``value`` as a string, refusing embedded control characters."""
    text = str(value or "")
    if CONTROL_CHARACTERS.search(text):
        raise HardeningViolation(f"{field} contains control characters")
    return text


def strip_control_characters(value: Any) -> str:
    """Remove control characters instead of refusing the value.

    For header filenames, where refusing would turn a cosmetic problem in an
    upload name into a failed download.
    """
    return CONTROL_CHARACTERS.sub("", str(value or ""))
