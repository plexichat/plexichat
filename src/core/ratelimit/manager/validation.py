"""Input validation for the rate-limit manager.

Everything a caller can influence is normalized or rejected here before it
reaches an algorithm or a storage backend. The important security rules:

* ``cost`` must be a non-negative integer. A negative cost used to refund
  token-bucket tokens (or decrement fixed/sliding/leaky counters), which
  bypassed every algorithm.
* window sizes must be positive and finite; a zero window caused a
  division-by-zero in the token-bucket refill path.
* limits must be non-negative and finite.
* caller-supplied config objects are validated before use.
* custom counters may not target reserved bucket-state fields (``tokens``,
  ``window_start``, ...), which would let a caller rewrite limiter state.
"""

import math
import operator
from typing import Any, Callable, Optional

from ..models import RateLimitConfig

_RESERVED_COUNTER_FIELDS = frozenset(
    {
        "key",
        "bucket_type",
        "tokens",
        "last_update",
        "window_start",
        "request_count",
        "hourly_count",
        "hourly_reset",
        "daily_count",
        "daily_reset",
        "timestamps",
    }
)

_MAX_COUNTER_KEY_LENGTH = 1024
_MAX_COUNTER_FIELD_LENGTH = 128


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_finite(value: Any) -> bool:
    if not _is_number(value):
        return False
    try:
        return math.isfinite(value)
    except (OverflowError, TypeError):
        return False


def normalise_cost(cost: int) -> int:
    """Validate and return an integer request cost.

    Zero is allowed (some callers use it for a read-only probe), but a
    negative or non-integer cost is rejected rather than silently clamped:
    accepting ``-1`` would refund quota and disable the limiter.
    """
    if isinstance(cost, bool):
        raise TypeError("rate limit cost must be an integer, not bool")
    try:
        value = operator.index(cost)
    except TypeError as exc:
        raise TypeError("rate limit cost must be an integer") from exc
    if value < 0:
        raise ValueError("rate limit cost must be non-negative")
    return value


def validate_multiplier(value: float, name: str) -> float:
    """Validate a non-negative, finite multiplier."""
    if not _is_finite(value):
        raise ValueError(f"{name} must be a finite number")
    numeric = float(value)
    if numeric < 0:
        raise ValueError(f"{name} must be non-negative")
    return numeric


def validate_config(
    config: RateLimitConfig, *, name: str = "rate limit config"
) -> RateLimitConfig:
    """Validate a rate-limit configuration.

    ``requests`` may be zero (an explicit "block everything" policy), but
    must not be negative. ``window_seconds`` must be strictly positive;
    otherwise the token-bucket refill rate divides by zero.
    """
    if not isinstance(config, RateLimitConfig):
        raise TypeError(f"{name} must be a RateLimitConfig")

    if not _is_finite(config.requests) or config.requests < 0:
        raise ValueError(f"{name}.requests must be a finite, non-negative number")
    if not _is_finite(config.burst) or config.burst < 0:
        raise ValueError(f"{name}.burst must be a finite, non-negative number")
    if not _is_finite(config.window_seconds) or config.window_seconds <= 0:
        raise ValueError(f"{name}.window_seconds must be a finite positive number")

    for field_name in ("hourly_limit", "daily_limit"):
        value: Optional[int] = getattr(config, field_name)
        if value is None:
            continue
        if not _is_finite(value) or value < 0:
            raise ValueError(
                f"{name}.{field_name} must be a finite, non-negative number or None"
            )

    return config


def validate_bypass_check(bypass_check: Callable[..., bool]) -> None:
    """Reject a non-callable bypass hook before it can be invoked."""
    if not callable(bypass_check):
        raise TypeError("bypass_check must be callable")


def validate_custom_counter_args(key: str, field: str, ttl: Any) -> None:
    """Validate arguments for :meth:`increment_custom_usage`."""
    if not isinstance(key, str) or not key:
        raise ValueError("counter key must be a non-empty string")
    if len(key) > _MAX_COUNTER_KEY_LENGTH:
        raise ValueError("counter key is too long")
    if not isinstance(field, str) or not field:
        raise ValueError("counter field must be a non-empty string")
    if len(field) > _MAX_COUNTER_FIELD_LENGTH:
        raise ValueError("counter field is too long")
    if field in _RESERVED_COUNTER_FIELDS:
        raise ValueError(f"counter field {field!r} is reserved for limiter state")
    if not _is_finite(ttl) or ttl < 0:
        raise ValueError("counter ttl must be a finite, non-negative number")


def normalise_amount(amount: int) -> int:
    """Validate a custom-counter increment amount."""
    if isinstance(amount, bool):
        raise TypeError("counter amount must be an integer, not bool")
    try:
        value = operator.index(amount)
    except TypeError as exc:
        raise TypeError("counter amount must be an integer") from exc
    if value < 0:
        raise ValueError("counter amount must be non-negative")
    return value
