"""Bucket-key generation.

Storage keys are built from caller-influenced components (route, IP,
resource/webhook ids). Each component is escaped, bounded, and hashed when
oversized so a single request can never create an unbounded key or collapse
neighbouring components into each other.

Colons are percent-escaped inside components. Without this a route such as
``GET /x:u:123`` would embed the literal segment ``u:123`` in its bucket
key, and :meth:`RateLimitManager.reset_user` would then treat that route
bucket as belonging to user 123.
"""

import hashlib
from typing import Optional

from ..models import BucketType
from .constants import (
    MAX_COMPONENT_KEY_LENGTH,
    MAX_ROUTE_KEY_LENGTH,
    MAX_TOTAL_KEY_LENGTH,
)


def _digest(value: str, length: int = 16) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:length]


def _escape_component(value: str) -> str:
    """Percent-escape the key separators used by bucket keys."""
    return value.replace("%", "%25").replace(":", "%3A")


def _bound(value: str, max_length: int) -> str:
    """Truncate an overlong component and append a stable hash suffix."""
    if len(value) <= max_length:
        return value
    budget = max(1, max_length - 17)
    return f"{value[:budget]}#{_digest(value)}"


def generate_bucket_key(
    bucket_type: BucketType,
    *,
    user_id: Optional[int] = None,
    ip_address: Optional[str] = None,
    route: Optional[str] = None,
    resource_id: Optional[int] = None,
    webhook_id: Optional[int] = None,
) -> str:
    """Generate a unique, bounded bucket key."""
    parts = [bucket_type.value]

    if user_id is not None:
        parts.append(f"u:{_bound(str(user_id), MAX_COMPONENT_KEY_LENGTH)}")
    elif ip_address is not None:
        ip_component = _bound(
            _escape_component(str(ip_address)), MAX_COMPONENT_KEY_LENGTH
        )
        parts.append(f"ip:{ip_component}")

    if route:
        route_component = _bound(_escape_component(str(route)), MAX_ROUTE_KEY_LENGTH)
        parts.append(f"r:{route_component}")

    if resource_id is not None:
        parts.append(f"res:{_bound(str(resource_id), MAX_COMPONENT_KEY_LENGTH)}")
    if webhook_id is not None:
        parts.append(f"wh:{_bound(str(webhook_id), MAX_COMPONENT_KEY_LENGTH)}")

    key = ":".join(parts)
    if len(key) > MAX_TOTAL_KEY_LENGTH:
        key = _bound(key, MAX_TOTAL_KEY_LENGTH)
    return key


def generate_bucket_id(key: str) -> str:
    """Generate a short, non-reversible bucket id for response headers."""
    return _digest(key)
