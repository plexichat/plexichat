"""
IP blacklist management module.

This module handles IP blocking, unblocking, checking, and listing.
"""

from typing import Optional, List, Dict, Any
from ._lazy import _get_auth_manager


def block_ip(
    ip_address: str,
    reason: Optional[str] = None,
    blocked_by: Optional[int] = None,
    duration_hours: Optional[int] = None,
) -> bool:
    """Block an IP address."""
    return (
        _get_auth_manager()
        .get_instance()
        .block_ip(ip_address, reason, blocked_by, duration_hours)
    )


def unblock_ip(ip_address: str) -> bool:
    """Unblock an IP address."""
    return _get_auth_manager().get_instance().unblock_ip(ip_address)


def is_ip_blocked(ip_address: str) -> bool:
    """Check if an IP address is blocked."""
    return _get_auth_manager().get_instance().is_ip_blocked(ip_address)


def get_blocked_ips() -> List[Dict[str, Any]]:
    """Get all blocked IPs."""
    return _get_auth_manager().get_instance().get_blocked_ips()


__all__ = [
    "block_ip",
    "unblock_ip",
    "is_ip_blocked",
    "get_blocked_ips",
]
