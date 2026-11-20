"""Rules for integers that are interpolated or drive resource use.

Configuration and query values reach ``PRAGMA``/``LIMIT`` clauses and buffer
sizes through plain arithmetic; "a very large integer" is not a harmless typo,
and a negative one is not either.
"""

from __future__ import annotations

import math
import re
from typing import Any, Optional

from .errors import HardeningViolation

#: Largest value a snowflake identifier can carry.
SNOWFLAKE_MAX = 2**63 - 1

#: Plain ASCII decimal integer, with an optional sign. ``int(str)`` is much
#: more permissive than this: it accepts Unicode decimal digits (``"٤٢"`` and
#: ``"４２"`` both become 42) and PEP 515 underscores (``"1_0"`` becomes 10).
#: An upstream ASCII-only validator (or a different language's ``\d``) reads
#: those same bytes as invalid, so accepting them here creates a parser
#: differential -- a homoglyph id can pass this gate yet be interpreted
#: differently by the next hop. Numeric input from a path/query/config has no
#: reason to use anything but ASCII digits.
_ASCII_INTEGER = re.compile(r"[+-]?[0-9]+")


_MAX_INTEGER_DIGITS = 4096


def bounded_int(
    value: Any,
    field: str,
    *,
    minimum: int,
    maximum: int,
    default: Optional[int] = None,
) -> int:
    """Coerce ``value`` to an int inside ``[minimum, maximum]``.

    ``int()`` alone is not a safe coercion here: it silently truncates a
    fraction (``10.9`` satisfied ``maximum=10``, ``-0.5`` satisfied
    ``minimum=0``), accepts booleans as ``1``/``0``, raises a bare
    ``OverflowError`` for ``inf``, and treats Unicode digit homoglyphs and
    underscore separators as numbers. A value that is not a plain integer is
    rejected instead of being quieted into range.
    """
    if value is None:
        if default is None:
            raise HardeningViolation(f"{field} is required")
        return default
    if isinstance(value, bool):
        raise HardeningViolation(f"{field} must be an integer")
    if isinstance(value, float):
        # An integral float is fine (config loaders sometimes emit ``1.0``),
        # but a fraction must never be truncated into an allowed range.
        if not math.isfinite(value) or not value.is_integer():
            raise HardeningViolation(f"{field} must be an integer")
        number = int(value)
    elif isinstance(value, str):
        # Do not let int()'s permissive string grammar turn a homoglyph or an
        # underscore-separated token into a number the caller never wrote in
        # ASCII. Surrounding whitespace is the only tolerance kept (int()
        # always allowed it), everything else must be an ASCII integer.
        stripped = value.strip()
        if not _ASCII_INTEGER.fullmatch(stripped):
            raise HardeningViolation(f"{field} must be an integer")
        # int() raises an uncaught ValueError past CPython's 4300-digit
        # conversion cap, so a 5000-digit token escaped as a 500 instead of a
        # validation error. Nothing this long is in range for any caller.
        if len(stripped.lstrip("+-")) > _MAX_INTEGER_DIGITS:
            raise HardeningViolation(f"{field} is outside the allowed range")
        number = int(stripped)
    else:
        try:
            number = int(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise HardeningViolation(f"{field} must be an integer") from exc
    if number < minimum or number > maximum:
        raise HardeningViolation(f"{field} is outside the allowed range")
    return number


def parse_id(value: Any, field: str = "id") -> int:
    """Parse a snowflake-style identifier from a path or query string.

    ``int(raw)`` accepted ``99000000000000000000`` and negative values, which
    then flowed into lookups and offset arithmetic; an identifier has a real
    range.
    """
    return bounded_int(value, field, minimum=1, maximum=SNOWFLAKE_MAX)


def clamp_finite_float(
    value: Any,
    minimum: float,
    maximum: float,
    *,
    default: float,
) -> float:
    """Clamp a numeric value into ``[minimum, maximum]``, failing *safe*.

    The idiomatic ``max(minimum, min(maximum, float(value)))`` is not safe for
    non-finite input: every comparison against ``NaN`` is False, so
    ``min(maximum, nan)`` returns ``maximum`` and the loudest possible value
    (e.g. full volume) is silently produced from garbage. ``-inf``/``inf``
    and overflowing integers are mishandled the same way, and the naive form
    also accepts ``bool`` as ``1``/``0``. Values that are not finite numbers
    instead degrade to ``default`` here, which callers pick as the quiet/safe
    end of their range.
    """
    if isinstance(value, bool):
        return default
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    if not math.isfinite(number):
        return default
    return max(minimum, min(number, maximum))
