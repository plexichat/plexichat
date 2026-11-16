"""Inbound boundary validation for the gateway.

Every value that arrives on a WebSocket frame (or in a Join HTTP body) is
attacker-controlled JSON. This module is the one place where those values are
checked against the defense-in-depth helpers in
:mod:`src.core.security.hardening` before they can drive parsing, fan-out or
persistence.

The rules are deliberately conservative: an oversized, ill-typed or deeply
nested value is refused rather than being "fixed up" and allowed through. A
malformed client may lose a single frame; the server never pays for unbounded
parsing or allocation on its behalf.
"""

from __future__ import annotations

from typing import Any

from src.core.security.hardening import HardeningViolation, bounded_int

# --- Frame / message size bounds (commit 1) ---------------------------------

#: Absolute ceiling for a single inbound frame, independent of configuration.
#: An operator may configure a *smaller* limit; a larger one is clamped here so
#: a bad config entry cannot silently disable the guard.
MAX_FRAME_BYTES = 256 * 1024

#: Ceiling for the decompressed form of a single frame.
MAX_DECOMPRESSED_BYTES = 1024 * 1024

#: Structural bounds applied to an already-decoded frame. JSON parsing itself
#: is bounded by the byte ceilings above, but a small frame can still describe
#: a pathologically deep or wide object (which then blows up recursive helpers
#: such as ``stringify_snowflake_ids``), so the decoded shape is bounded too.
MAX_PARSED_DEPTH = 12
MAX_PARSED_KEYS = 128
MAX_PARSED_STRING_BYTES = 64 * 1024


def validate_frame_bytes(data: bytes) -> bool:
    """Whether a raw frame is small enough to parse.

    This is called on the raw ``text``/``bytes`` payload *before* ``json.loads``
    so a hostile frame is rejected without ever being decoded.
    """
    try:
        size = len(data)
    except TypeError:
        return False
    return 0 <= size <= MAX_FRAME_BYTES


def validate_parsed_structure(value: Any, _depth: int = 0) -> None:
    """Reject decoded frames whose shape would make downstream work unbounded.

    Raises :class:`HardeningViolation` when nesting depth, object width or an
    individual string value exceeds its ceiling. Containers are walked once;
    the function never mutates ``value``.
    """
    if _depth > MAX_PARSED_DEPTH:
        raise HardeningViolation("gateway frame nesting too deep")
    if isinstance(value, dict):
        if len(value) > MAX_PARSED_KEYS:
            raise HardeningViolation("gateway frame has too many keys")
        for key, item in value.items():
            if isinstance(key, str) and len(key.encode("utf-8", "ignore")) > 256:
                raise HardeningViolation("gateway frame key too long")
            validate_parsed_structure(item, _depth + 1)
    elif isinstance(value, (list, tuple)):
        if len(value) > MAX_PARSED_KEYS:
            raise HardeningViolation("gateway frame sequence too long")
        for item in value:
            validate_parsed_structure(item, _depth + 1)
    elif isinstance(value, str):
        if len(value.encode("utf-8", "ignore")) > MAX_PARSED_STRING_BYTES:
            raise HardeningViolation("gateway frame string too long")


def clamp_configured_limit(value: Any, field: str, ceiling: int, default: int) -> int:
    """Return a config-provided byte limit clamped into ``[1, ceiling]``."""
    return bounded_int(value, field, minimum=1, maximum=ceiling, default=default)
