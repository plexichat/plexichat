"""Rules for integers that are interpolated or drive resource use.

Configuration and query values reach ``PRAGMA``/``LIMIT`` clauses and buffer
sizes through plain arithmetic; "a very large integer" is not a harmless typo,
and a negative one is not either.
"""

from __future__ import annotations

import math
from typing import Any, Optional

from .errors import HardeningViolation

#: Largest value a snowflake identifier can carry.
SNOWFLAKE_MAX = 2**63 - 1


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
    ``minimum=0``), accepts booleans as ``1``/``0``, and raises a bare
    ``OverflowError`` for ``inf``. A value that is not an integer is rejected
    instead of being quieted into range.
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
