"""Hardened coercion for rate-limit configuration values.

``utils.config`` returns whatever an operator put in ``config.yaml``. A typo
(``requests: -5``), a float with a fractional part, a boolean (``True`` is an
``int`` in Python), or a string copied from a dashboard can all reach arithmetic
that sizes token buckets and storage rows. The managers already validate a
``RateLimitConfig`` *object*; this module validates the *raw values* before an
object is built, so a bad config cannot smuggle an overflow or a negative into
the limiter in the first place.

The coercion functions are deliberately forgiving at the boundary: an invalid
value falls back to a known-good default and logs a warning instead of taking
the server down at startup. A shipped deployment with a stale key keeps
running with safe limits; the warning makes the misconfiguration visible.
"""

from __future__ import annotations

from typing import Any, Optional

from src.core.security.hardening import HardeningViolation, bounded_int

# Conservative ceilings for the coarse scopes. These are far above any
# documented default but well below anything that would overflow bucket
# arithmetic or let one config key reserve an unbounded keyspace.
MAX_SCOPE_REQUESTS = 10_000_000
MAX_SCOPE_BURST = 2_000_000
MAX_SCOPE_LIMIT = 50_000_000


def _warn(field: str, value: Any, reason: str, default: Any) -> None:
    """Surface a rejected config value without importing the logger eagerly."""
    try:
        import utils.logger as logger

        logger.warning(
            f"Invalid rate-limit config for {field!r} ({value!r}): {reason}; "
            f"using default {default!r}"
        )
    except Exception:  # pragma: no cover - logging must never break config
        pass


def coerce_config_int(
    value: Any,
    field: str,
    *,
    minimum: int,
    maximum: int,
    default: int,
) -> int:
    """Return ``value`` as an int in ``[minimum, maximum]`` or ``default``.

    ``bool`` is refused explicitly (``int(True) == 1`` would otherwise turn a
    mistaken ``requests: true`` into a one-request bucket), as are floats that
    carry a fractional part and strings that are not plain integers.
    """
    if value is None:
        return default
    if isinstance(value, bool):
        _warn(field, value, "booleans are not request counts", default)
        return default
    if isinstance(value, float) and not value.is_integer():
        _warn(field, value, "fractional floats are not integer counts", default)
        return default
    try:
        result = bounded_int(value, field, minimum=minimum, maximum=maximum)
    except (HardeningViolation, TypeError, ValueError, OverflowError):
        _warn(field, value, f"must be an int in [{minimum}, {maximum}]", default)
        return default
    return result


def coerce_config_float(
    value: Any,
    field: str,
    *,
    minimum: float,
    maximum: float,
    default: float,
) -> float:
    """Return ``value`` as a finite float in ``[minimum, maximum]``."""
    if value is None:
        return default
    if isinstance(value, bool):
        _warn(field, value, "booleans are not durations", default)
        return default
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        _warn(field, value, "not a number", default)
        return default
    if number != number or number in (float("inf"), float("-inf")):
        _warn(field, value, "not finite", default)
        return default
    if number < minimum or number > maximum:
        _warn(field, value, f"outside [{minimum}, {maximum}]", default)
        return default
    return number


def coerce_optional_int(
    value: Any,
    field: str,
    *,
    maximum: int,
    default: Optional[int] = None,
) -> Optional[int]:
    """Coerce an optional non-negative limit (``hourly_limit``/``daily_limit``)."""
    if value is None:
        return default
    # An empty string is how a nearly-empty config block sometimes surfaces.
    if isinstance(value, str) and not value.strip():
        return default
    # ``-1`` is outside the valid [0, maximum] range, so it doubles as a
    # sentinel: a real coerced value can never equal it. This keeps the strict
    # int coercion in one place while allowing ``None`` as the fallback.
    sentinel = -1
    result = coerce_config_int(
        value, field, minimum=0, maximum=maximum, default=sentinel
    )
    return default if result == sentinel else result


def coerce_requests(value: Any, field: str, default: int) -> int:
    """Coerce a ``requests`` count."""
    return coerce_config_int(
        value, field, minimum=0, maximum=MAX_SCOPE_REQUESTS, default=default
    )


def coerce_burst(value: Any, field: str, default: int) -> int:
    """Coerce a ``burst`` count."""
    return coerce_config_int(
        value, field, minimum=0, maximum=MAX_SCOPE_BURST, default=default
    )


def coerce_window(value: Any, field: str, default: float) -> float:
    """Coerce a ``window_seconds`` duration (strictly positive)."""
    return coerce_config_float(
        value, field, minimum=1e-6, maximum=86_400.0, default=default
    )


def coerce_multiplier(value: Any, field: str, default: float) -> float:
    """Coerce a bot/webhook multiplier to a sane non-negative float."""
    return coerce_config_float(
        value, field, minimum=0.0, maximum=1000.0, default=default
    )


__all__ = [
    "MAX_SCOPE_REQUESTS",
    "MAX_SCOPE_BURST",
    "MAX_SCOPE_LIMIT",
    "coerce_config_int",
    "coerce_config_float",
    "coerce_optional_int",
    "coerce_requests",
    "coerce_burst",
    "coerce_window",
    "coerce_multiplier",
]
