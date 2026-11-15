"""Defense-in-depth helpers for values that cross a trust boundary.

The same four questions kept being answered slightly differently at each call
site: what may appear in an HTTP header, what may name a SQL object, what may be
joined onto a filesystem base, and what an integer read from configuration may
be. Centralising them means a new call site cannot quietly pick the weaker
answer, and the answers can be tested once.

Helpers raise :class:`HardeningViolation` (a ``ValueError``) rather than
returning a silently corrected value -- except
:func:`sanitize_header_filename`, whose whole job is to produce a safe fallback
for a header, where refusing is not an option.
"""

from __future__ import annotations

import os
import re
from typing import Any, Optional

#: A SQL object name: letters, digits and underscores, never starting with a
#: digit, bounded so a hostile name cannot be used to smuggle syntax.
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")

#: Everything outside this set is collapsed in a header filename.
_FILENAME_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")

#: C0 controls plus DEL: the characters that break out of an HTTP header.
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


class HardeningViolation(ValueError):
    """A value violated a defense-in-depth invariant."""


def sanitize_header_filename(
    name: Any, fallback: str = "attachment", max_length: int = 120
) -> str:
    """Return ``name`` as something safe to place in a header.

    Strips control characters (the header-injection vector), takes only the
    final path component, collapses anything outside ``[A-Za-z0-9._-]``, and
    bounds the length. Always returns a usable filename.
    """
    cleaned = _CONTROL.sub("", str(name or ""))
    cleaned = cleaned.replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = _FILENAME_UNSAFE.sub("_", cleaned).strip(". _")
    if not cleaned or cleaned in {".", ".."}:
        return fallback
    return cleaned[:max_length]


def content_disposition(filename: Any, *, inline: bool = False) -> str:
    """Build a ``Content-Disposition`` value from an untrusted filename."""
    safe = sanitize_header_filename(filename)
    return f'{"inline" if inline else "attachment"}; filename="{safe}"'


def is_identifier(value: Any) -> bool:
    """Whether ``value`` is safe to interpolate as a SQL object name."""
    return isinstance(value, str) and bool(_IDENTIFIER.match(value))


def require_identifier(value: Any, field: str = "identifier") -> str:
    """Return ``value`` if it is a safe SQL object name, else raise.

    Used before interpolating a table, column, index or trigger name into
    ``PRAGMA``/``ALTER``/``DROP`` statements, which cannot take parameters.
    """
    if not is_identifier(value):
        raise HardeningViolation(f"{field} is not a valid SQL identifier")
    return value


def safe_join(base: str, *parts: str) -> str:
    """Join ``parts`` onto ``base`` and refuse anything that escapes it.

    Compares resolved paths, so ``..`` components and symlinked escapes both
    fail rather than relying on a string prefix check.
    """
    base_resolved = os.path.realpath(base)
    candidate = os.path.realpath(os.path.join(base_resolved, *parts))
    if candidate != base_resolved and not candidate.startswith(base_resolved + os.sep):
        raise HardeningViolation("path escapes its base directory")
    return candidate


def bounded_int(
    value: Any,
    field: str,
    *,
    minimum: int,
    maximum: int,
    default: Optional[int] = None,
) -> int:
    """Coerce ``value`` to an int inside ``[minimum, maximum]``.

    Configuration and query values reach ``PRAGMA``/``LIMIT`` clauses through
    string interpolation, so "a very large integer" is not a harmless typo.
    """
    if value is None:
        if default is None:
            raise HardeningViolation(f"{field} is required")
        return default
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise HardeningViolation(f"{field} must be an integer") from exc
    if number < minimum or number > maximum:
        raise HardeningViolation(f"{field} is outside the allowed range")
    return number


def reject_control_characters(value: Any, field: str = "value") -> str:
    """Return ``value`` as a string, refusing embedded control characters.

    For values copied straight into a response header (checksums, request ids)
    where a CR/LF would split the header.
    """
    text = str(value or "")
    if _CONTROL.search(text):
        raise HardeningViolation(f"{field} contains control characters")
    return text
