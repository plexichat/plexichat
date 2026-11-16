"""Declared-schema validation for feature flag keys and values.

``features.has_feature`` used to pass a caller-supplied string straight to
``getattr``. A key such as ``__class__`` therefore resolved to an attribute
rather than a flag. Keys are now checked against a closed grammar, values are
bounded by type, and the helpers are shared by the admin write path.
"""

from __future__ import annotations

import re
from typing import Any, Dict

from src.core.security.hardening import (
    HardeningViolation,
    bounded_int,
    reject_control_characters,
)

FEATURE_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
MAX_FEATURE_VALUE_LENGTH = 256
MAX_FEATURE_VALUE_INT = 2**31 - 1
MAX_FLAGS_PER_REQUEST = 100

# Attribute names that must never be reachable through a dynamic lookup.
RESERVED_FLAG_KEYS = frozenset(
    {
        "__class__",
        "__dict__",
        "__doc__",
        "__globals__",
        "__init__",
        "__module__",
        "__subclasses__",
    }
)


def validate_feature_key(value: Any, *, field: str = "feature") -> str:
    """Return a safe feature-flag key or raise :class:`HardeningViolation`."""
    key = reject_control_characters(value, field)
    if key in RESERVED_FLAG_KEYS or not FEATURE_KEY_PATTERN.fullmatch(key):
        raise HardeningViolation(f"{field} is not a declared feature key")
    return key


def validate_feature_value(value: Any, *, field: str = "value") -> Any:
    """Validate a scalar feature-flag value against its declared types."""
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, int):
        return bounded_int(value, field, minimum=0, maximum=MAX_FEATURE_VALUE_INT)
    if isinstance(value, str):
        text = reject_control_characters(value, field)
        if len(text) > MAX_FEATURE_VALUE_LENGTH:
            raise HardeningViolation(
                f"{field} exceeds {MAX_FEATURE_VALUE_LENGTH} characters"
            )
        return text
    raise HardeningViolation(f"{field} has an unsupported type")


def validate_feature_flags(mapping: Any, *, field: str = "flags") -> Dict[str, Any]:
    """Validate a bounded ``{key: value}`` feature-flag mapping."""
    if not isinstance(mapping, dict):
        raise HardeningViolation(f"{field} must be a mapping")
    if len(mapping) > MAX_FLAGS_PER_REQUEST:
        raise HardeningViolation(
            f"{field} may contain at most {MAX_FLAGS_PER_REQUEST} entries"
        )
    result: Dict[str, Any] = {}
    for key, value in mapping.items():
        safe_key = validate_feature_key(key, field=f"{field} key")
        result[safe_key] = validate_feature_value(value, field=f"{field}[{safe_key}]")
    return result
