"""Principal normalization for per-principal rate limiting.

Bucket keys are only as trustworthy as the identity components used to build
them. Callers of the manager (middleware, decorators, route handlers) can hand
us values that originated in request headers or route parameters; if those
values are interpolated into a key verbatim, a client can:

* forge *extra key segments* (a ``user_id`` of ``"1:u:2"`` produces a key that
  looks like it belongs to two users),
* create a fresh bucket per request by rotating a free-form identifier, or
* make one logical principal appear as many different ones.

This module reduces every identity component to a bounded, canonical form
before it reaches :mod:`.keys`. The rules are deliberately lossy in the safe
direction: a malformed identifier is *not* an unlimited request, it is simply
treated as unauthenticated and keyed by the remaining trusted principal (the
IP), or by a shared sentinel bucket.
"""

from __future__ import annotations

from typing import Optional, Tuple

from .constants import MAX_COMPONENT_KEY_LENGTH

# Identifiers are stored as signed 64-bit integers in the database. Anything
# wider is an attacker-supplied literal, not a real principal, and must not be
# allowed to fan out into a distinct bucket per digit string.
MAX_PRINCIPAL_ID = (1 << 63) - 1

# A single shared bucket for malformed/oversized IP strings. Rotating garbage
# input cannot grow the keyspace because every such request lands here.
INVALID_IP_PRINCIPAL = "invalid-ip"


def normalise_principal_id(value: object) -> Optional[int]:
    """Return a canonical non-negative principal id, or ``None`` if invalid.

    ``bool`` is rejected explicitly (``True`` would otherwise be user 1), as
    are floats, negative values, oversized ints and non-digit strings. A
    decimal string of digits is accepted because some auth backends store ids
    as text; normalizing it to an int makes ``"007"`` and ``7`` the same
    principal instead of two independent buckets.
    """
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        candidate = value
    elif isinstance(value, str):
        text = value.strip()
        if not text or not text.isascii() or not text.isdigit():
            return None
        try:
            candidate = int(text)
        except (ValueError, OverflowError):
            return None
    else:
        return None
    if candidate < 0 or candidate > MAX_PRINCIPAL_ID:
        return None
    return candidate


def normalise_resource_id(value: object) -> Optional[int]:
    """Canonical numeric resource/webhook id; ``None`` disables that scope.

    Falling back to the route scope is safe: the request is still limited,
    just at a coarser granularity. Never let a malformed id select a distinct
    per-resource bucket.
    """
    return normalise_principal_id(value)


def normalise_ip_address(value: object) -> Optional[str]:
    """Return a bounded IP principal.

    Non-string or empty inputs yield ``None`` (no IP bucket). Oversized
    values collapse onto :data:`INVALID_IP_PRINCIPAL` rather than producing a
    fresh hashed bucket, so header rotation cannot grow the keyspace. IPv6
    literals keep their colons -- :mod:`.keys` escapes key separators, so a
    colon cannot forge an extra segment here (unlike the legacy user-id path).
    Control characters are rejected outright.
    """
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate:
        return None
    if len(candidate) > MAX_COMPONENT_KEY_LENGTH:
        return INVALID_IP_PRINCIPAL
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in candidate):
        return INVALID_IP_PRINCIPAL
    return candidate


def normalise_principal(
    user_id: object, ip_address: object
) -> Tuple[Optional[int], Optional[str]]:
    """Resolve a request to exactly one trusted principal.

    An authenticated user is keyed *only* by their id: the route bucket must
    not change when the client rotates ``X-Forwarded-For``. Unauthenticated
    requests fall back to the (proxy-trusted) IP. When neither is usable the
    route bucket is shared, which is still a bounded, limited bucket.
    """
    principal_id = normalise_principal_id(user_id)
    if principal_id is not None:
        return principal_id, None
    return None, normalise_ip_address(ip_address)


__all__ = [
    "INVALID_IP_PRINCIPAL",
    "MAX_PRINCIPAL_ID",
    "normalise_ip_address",
    "normalise_principal",
    "normalise_principal_id",
    "normalise_resource_id",
]
