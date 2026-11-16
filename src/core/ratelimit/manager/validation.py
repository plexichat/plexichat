"""Input validation for the rate-limit manager.

Everything a caller can influence is normalized or rejected here before it
reaches an algorithm or a storage backend. The important security rules:

* ``cost`` must be a non-negative integer, bounded above. A negative cost
  used to refund token-bucket tokens (or decrement fixed/sliding/leaky
  counters), which bypassed every algorithm.
* window sizes must be positive, finite and capped; a zero window caused a
  division-by-zero in the token-bucket refill path, while an enormous one
  made the refill rate effectively zero.
* limits and counts must be non-negative *and* bounded. A finite-but-absurd
  value is as harmful as a negative one: it sizes token buckets and storage
  rows from attacker input.
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

# Hard ceilings for every caller-influenced number the limiter uses. All sit
# far above any documented default; they only stop oversized values from
# making bucket arithmetic / storage sizing meaningless.
MAX_REQUESTS = 10_000_000
MAX_BURST = 2_000_000
MAX_WINDOW_SECONDS = 86_400.0  # one day
MAX_LIMIT = 50_000_000
MAX_COST = 1_000_000
MAX_AMOUNT = 1_000_000
MAX_MULTIPLIER = 1_000.0
MAX_TTL = 2_592_000.0  # 30 days
MAX_RETRY_AFTER_PRECISION = 6

# Python ints do not overflow, but an int wider than 64 bits cannot be a
# legitimate count here and signals an attacker-supplied giant literal.
_MAX_INT_BITS = 64


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_finite(value: Any) -> bool:
    if not _is_number(value):
        return False
    try:
        return math.isfinite(value)
    except (OverflowError, TypeError):
        return False


def _check_bounded(
    value: Any,
    field: str,
    *,
    minimum: float,
    maximum: float,
    integer: bool = False,
) -> None:
    """Raise ``ValueError`` unless ``value`` is a bounded finite number.

    ``integer`` additionally rejects bools and integers wider than 64 bits.
    """
    if integer:
        if isinstance(value, bool) or not _is_number(value):
            raise ValueError(f"{field} must be a number")
        if isinstance(value, float) and not value.is_integer():
            raise ValueError(f"{field} must be an integer")
        if isinstance(value, int) and value.bit_length() > _MAX_INT_BITS:
            raise ValueError(f"{field} is too large")
    elif not _is_finite(value):
        raise ValueError(f"{field} must be a finite number")
    numeric = float(value)
    if numeric < minimum or numeric > maximum:
        raise ValueError(f"{field} is outside the allowed range")


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
    if value > MAX_COST:
        raise ValueError("rate limit cost is too large")
    return value


def validate_multiplier(value: float, name: str) -> float:
    """Validate a non-negative, finite, bounded multiplier."""
    _check_bounded(value, name, minimum=0.0, maximum=MAX_MULTIPLIER)
    return float(value)


def validate_config(
    config: RateLimitConfig, *, name: str = "rate limit config"
) -> RateLimitConfig:
    """Validate a rate-limit configuration.

    ``requests`` may be zero (an explicit "block everything" policy), but
    must not be negative. ``window_seconds`` must be strictly positive;
    otherwise the token-bucket refill rate divides by zero. Every field is
    additionally capped so an oversized value cannot blow up arithmetic.
    """
    if not isinstance(config, RateLimitConfig):
        raise TypeError(f"{name} must be a RateLimitConfig")

    _check_bounded(config.requests, f"{name}.requests", minimum=0, maximum=MAX_REQUESTS)
    _check_bounded(config.burst, f"{name}.burst", minimum=0, maximum=MAX_BURST)
    _check_bounded(
        config.window_seconds,
        f"{name}.window_seconds",
        minimum=1e-6,
        maximum=MAX_WINDOW_SECONDS,
    )

    for field_name in ("hourly_limit", "daily_limit"):
        value: Optional[int] = getattr(config, field_name)
        if value is None:
            continue
        _check_bounded(value, f"{name}.{field_name}", minimum=0, maximum=MAX_LIMIT)

    _check_bounded(
        config.retry_after_precision,
        f"{name}.retry_after_precision",
        minimum=0,
        maximum=MAX_RETRY_AFTER_PRECISION,
        integer=True,
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
    _check_bounded(ttl, "counter ttl", minimum=0.0, maximum=MAX_TTL)


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
    if value > MAX_AMOUNT:
        raise ValueError("counter amount is too large")
    return value
