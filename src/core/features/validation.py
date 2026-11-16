"""Declared-schema validation for feature flag keys and values.

``features.has_feature`` used to pass a caller-supplied string straight to
``getattr``. A key such as ``__class__`` therefore resolved to an attribute
rather than a flag. Keys are now checked against a closed grammar, values are
bounded by type, and the helpers are shared by the admin write path.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from src.core.security.hardening import (
    HardeningViolation,
    bounded_int,
    parse_id,
    reject_control_characters,
)

FEATURE_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
MAX_FEATURE_VALUE_LENGTH = 256
MAX_FEATURE_VALUE_INT = 2**31 - 1
MAX_FLAGS_PER_REQUEST = 100

# Rollout controls. A percentage is closed to [0, 100] and a forced-target
# list is capped so a config typo cannot become a full-table scan.
MAX_ROLLOUT_PERCENTAGE = 100.0
MAX_ROLLOUT_TARGETS = 1000
ROLLOUT_BUCKETS = 10_000

# Tier config values arrive from YAML; bound each before it reaches a limit
# check. -1 means "unlimited" for the time-based fields.
TIER_LIMIT_BOUNDS = {
    "multiplier": (0, 100),
    "max_voice_minutes_per_day": (-1, 525_600),
    "max_video_minutes_per_day": (-1, 525_600),
    "max_file_uploads_per_day": (0, 100_000),
    "max_file_size_mb": (0, 1_000_000),
    "max_storage_mb": (0, 10**9),
    "max_servers": (0, 100_000),
    "max_message_length": (0, 100_000),
    "max_reactions_per_message": (0, 10_000),
    "max_pins_per_channel": (0, 100_000),
    "custom_emoji_slots": (0, 100_000),
}

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


def validate_rollout_percentage(
    value: Any, *, field: str = "rollout_percentage"
) -> float:
    """Return a percentage in ``[0, 100]``."""
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise HardeningViolation(f"{field} must be numeric") from exc
    if number < 0.0 or number > MAX_ROLLOUT_PERCENTAGE:
        raise HardeningViolation(
            f"{field} must be between 0 and {MAX_ROLLOUT_PERCENTAGE}"
        )
    return number


def validate_target_list(value: Any, *, field: str = "targets") -> List[int]:
    """Cap and validate a list of user IDs force-included in a rollout."""
    if value is None:
        return []
    try:
        candidates = list(value)
    except TypeError as exc:
        raise HardeningViolation(f"{field} must be a list") from exc
    if len(candidates) > MAX_ROLLOUT_TARGETS:
        raise HardeningViolation(
            f"{field} may contain at most {MAX_ROLLOUT_TARGETS} entries"
        )
    return [
        parse_id(candidate, f"{field}[{index}]")
        for index, candidate in enumerate(candidates)
    ]


def feature_enabled_for(
    user_id: Any,
    *,
    rollout_percentage: Any,
    targets: Optional[Any] = None,
    granted: bool = False,
) -> bool:
    """Deterministically decide whether a user is in a bounded rollout."""
    percentage = validate_rollout_percentage(rollout_percentage)
    target_ids = validate_target_list(targets)
    user = parse_id(user_id, "user_id")
    if granted or user in target_ids:
        return True
    bucket = user % ROLLOUT_BUCKETS
    return bucket < int(percentage * ROLLOUT_BUCKETS / 100)


def validate_tier_limits(config: Any) -> Dict[str, Any]:
    """Bound each numeric tier limit from configuration."""
    if not isinstance(config, dict):
        raise HardeningViolation("tier configuration must be a mapping")
    result: Dict[str, Any] = {}
    for key, raw in config.items():
        if key not in TIER_LIMIT_BOUNDS:
            continue
        if key == "multiplier":
            result[key] = validate_rollout_percentage(raw, field=key)
            continue
        minimum, maximum = TIER_LIMIT_BOUNDS[key]
        result[key] = bounded_int(raw, key, minimum=minimum, maximum=maximum)
    return result
