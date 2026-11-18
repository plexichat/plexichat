"""Rules for text that is copied into a header, log line or index as-is.

Control characters are the ones that break out of an HTTP header, forge a log
line, or make two parsers disagree about where a line ends. They are never
legitimate in a checksum, request id, URL or short machine token.

The forbidden set is deliberately wider than just C0 + DEL:

* ``\\x00-\\x1f`` -- C0 controls (CR, LF, TAB, NUL, ...), the classic header
  injection and log forging band.
* ``\\x7f-\\x9f`` -- DEL plus the C1 controls. In particular U+0085 (NEL, NEXT
  LINE) is treated as a line break by many log shippers, proxies and text
  decoders, so it is just as dangerous as LF for header splitting.
* ``\\u2028`` / ``\\u2029`` -- Unicode LINE SEPARATOR and PARAGRAPH SEPARATOR.
  JavaScript, many HTTP stacks and most log aggregators normalise these to a
  newline; a value that survives this check can still split a downstream
  header or forge a log entry.

Rejecting them all here keeps every consumer (headers, URLs, filenames, logs)
on the same side of the parser differential instead of trusting each one to
happen to reject the same characters.
"""

from __future__ import annotations

import re
from typing import Any

from .errors import HardeningViolation

CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f-\x9f\u2028\u2029]")


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
