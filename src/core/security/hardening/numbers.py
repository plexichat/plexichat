"""Rules for integers that are interpolated or drive resource use.

Configuration and query values reach ``PRAGMA``/``LIMIT`` clauses and buffer
sizes through plain arithmetic; "a very large integer" is not a harmless typo,
and a negative one is not either.
"""

from __future__ import annotations

from typing import Any, Optional

from .errors import HardeningViolation


def bounded_int(
    value: Any,
    field: str,
    *,
    minimum: int,
    maximum: int,
    default: Optional[int] = None,
) -> int:
    """Coerce ``value`` to an int inside ``[minimum, maximum]``."""
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
