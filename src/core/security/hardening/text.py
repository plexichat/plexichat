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

* bidi and other invisible format controls -- the Arabic letter mark, LRM/RLM,
  the Syriac abbreviation mark, the bidi embedding/override band, the isolate
  and legacy directional-format band, the word joiner and the BOM. These do not
  split a line, but they let one string *render* as another: a URL or header
  filename that passes here can show a reviewer a different host/name than the
  one actually stored and fetched. The request-schema boundary already rejects
  this same set via ``policy_types.UNSAFE_UNICODE``; keeping the two lists in
  lockstep closes the differential for the many callers that use these
  primitives directly. The zero-width joiner is deliberately excluded: it is
  part of ordinary emoji sequences.

Rejecting them all here keeps every consumer (headers, URLs, filenames, logs)
on the same side of the parser differential instead of trusting each one to
happen to reject the same characters.
"""

from __future__ import annotations

import re
from typing import Any

from .errors import HardeningViolation

CONTROL_CHARACTERS = re.compile(
    r"[\x00-\x1f\x7f-\x9f"  # C0, DEL and C1 controls (incl. NEL)
    r"\u061c\u200e\u200f\u070f"  # Arabic/LR/RL marks + Syriac abbreviation mark
    r"\u202a-\u202e"  # bidi embedding and overrides
    r"\u2066-\u206f"  # bidi isolates + legacy directional formats
    r"\u2028\u2029\u2060\ufeff]"  # line/para separators, word joiner, BOM
)


def reject_control_characters(value: Any, field: str = "value") -> str:
    """Return ``value`` as a string, refusing embedded control characters."""
    text = str(value or "")
    # Fast path: every codepoint in CONTROL_CHARACTERS is non-printable, so a
    # fully printable value cannot contain one. isprintable() is a C-level
    # scan that skips the regex engine on the overwhelmingly common case
    # (ordinary headers, log fields, URLs). Fails closed: a value with any
    # forbidden character is non-printable and still goes through the regex.
    if text.isprintable():
        return text
    if CONTROL_CHARACTERS.search(text):
        raise HardeningViolation(f"{field} contains control characters")
    return text


def strip_control_characters(value: Any) -> str:
    """Remove control characters instead of refusing the value.

    For header filenames, where refusing would turn a cosmetic problem in an
    upload name into a failed download.
    """
    return CONTROL_CHARACTERS.sub("", str(value or ""))
