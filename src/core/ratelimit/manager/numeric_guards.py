"""Overflow-safe arithmetic for derived rate-limit numbers.

Even with every input individually capped, the *combinations* the limiter
computes can exceed a safe range: ``effective_limit`` adds ``requests`` and
``burst``, and :meth:`RateLimitConfig.with_multiplier` multiplies both by a
caller-supplied factor. On a 64-bit runtime these do not raise -- they silently
wrap in the storage layer -- so the result must be checked before it is used
to size a bucket or a window.

These helpers fail closed with ``ValueError`` so the manager's existing error
handling turns an oversized derived value into a rejected request rather than
a bucket with a nonsensical capacity.
"""

from __future__ import annotations

from typing import Any

# Kept in sync with validation.py caps (duplicated here to avoid an import
# cycle with the manager package during module init).
_MAX_DERIVED = 50_000_000
_MAX_INT_BITS = 64


def _require_count(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be an integer")
    if value < 0:
        raise ValueError(f"{field} must be non-negative")
    if value.bit_length() > _MAX_INT_BITS:
        raise ValueError(f"{field} is too large")
    return value


def safe_add(a: Any, b: Any, *, field: str = "sum", cap: int = _MAX_DERIVED) -> int:
    """Return ``a + b`` after rejecting negatives, overflow and oversize."""
    left = _require_count(a, f"{field}.left")
    right = _require_count(b, f"{field}.right")
    if cap < 0 or cap.bit_length() > _MAX_INT_BITS:
        raise ValueError("cap must be a bounded non-negative integer")
    result = left + right
    if result > cap:
        raise ValueError(f"{field} exceeds the allowed cap")
    return result


def safe_multiply(
    value: Any, multiplier: Any, *, field: str = "product", cap: int = _MAX_DERIVED
) -> int:
    """Return ``int(value * multiplier)`` with overflow/format guards.

    ``multiplier`` must be a finite non-negative number; a boolean or an
    ``inf``/``nan`` is refused rather than coerced.
    """
    base = _require_count(value, f"{field}.value")
    if isinstance(multiplier, bool) or not isinstance(multiplier, (int, float)):
        raise ValueError(f"{field}.multiplier must be a number")
    if multiplier != multiplier or multiplier in (float("inf"), float("-inf")):
        raise ValueError(f"{field}.multiplier must be finite")
    if multiplier < 0:
        raise ValueError(f"{field}.multiplier must be non-negative")
    if isinstance(multiplier, int) and multiplier.bit_length() > _MAX_INT_BITS:
        raise ValueError(f"{field}.multiplier is too large")
    if cap < 0 or cap.bit_length() > _MAX_INT_BITS:
        raise ValueError("cap must be a bounded non-negative integer")
    result = int(base * multiplier)
    if result > cap:
        raise ValueError(f"{field} exceeds the allowed cap")
    return result


def guard_effective_limit(requests: Any, burst: Any) -> int:
    """Safe replacement for ``requests + burst`` in effective-limit math."""
    return safe_add(requests, burst, field="effective_limit")


__all__ = ["safe_add", "safe_multiply", "guard_effective_limit"]
