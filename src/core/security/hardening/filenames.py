"""Rules for filenames that reach a ``Content-Disposition`` header.

Two separate concerns live here and keep getting conflated at call sites: the
filename must not be able to *split the header* (CR/LF), and it must not be able
to *steer the client's save location* (path separators, quotes).
"""

from __future__ import annotations

import re
from typing import Any

from .text import strip_control_characters

#: Everything outside this set is collapsed in a header filename.
FILENAME_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def sanitize_header_filename(
    name: Any, fallback: str = "attachment", max_length: int = 120
) -> str:
    """Return ``name`` as something safe to place in a header.

    Strips control characters (the header-injection vector), takes only the
    final path component, collapses anything outside ``[A-Za-z0-9._-]``, and
    bounds the length. Always returns a usable filename.
    """
    cleaned = strip_control_characters(name)
    cleaned = cleaned.replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = FILENAME_UNSAFE.sub("_", cleaned).strip(". _")
    if not cleaned or cleaned in {".", ".."}:
        return fallback
    return cleaned[:max_length]


def content_disposition(filename: Any, *, inline: bool = False) -> str:
    """Build a ``Content-Disposition`` value from an untrusted filename."""
    safe = sanitize_header_filename(filename)
    return f'{"inline" if inline else "attachment"}; filename="{safe}"'
