"""
Capability/permission checking module.

This module provides functions to check and require specific capabilities/permissions
from token information.
"""

from .models import TokenInfo
from .permissions import has_permission
from .exceptions import PermissionDeniedError


def has_capability(token_info: TokenInfo, capability: str) -> bool:
    """Check if token has a specific capability/permission."""
    return has_permission(token_info.permissions, capability)


def require_capability(token_info: TokenInfo, capability: str) -> None:
    """Require a capability, raising PermissionDeniedError if missing."""
    if not has_permission(token_info.permissions, capability):
        raise PermissionDeniedError(f"Missing required permission: {capability}")


__all__ = [
    "has_capability",
    "require_capability",
]
