"""
Device management module.

This module handles device listing, renaming, and revocation for users.
"""

from typing import List
from .models import Device
from ._lazy import _get_auth_manager


def get_devices(user_id: int) -> List[Device]:
    """Get all known devices for a user."""
    return _get_auth_manager().get_instance().get_devices(user_id)


def rename_device(user_id: int, device_id: int, name: str) -> bool:
    """Rename a device."""
    return _get_auth_manager().get_instance().rename_device(user_id, device_id, name)


def revoke_device(user_id: int, device_id: int) -> bool:
    """Revoke a device and all its sessions."""
    return _get_auth_manager().get_instance().revoke_device(user_id, device_id)


__all__ = [
    "get_devices",
    "rename_device",
    "revoke_device",
]
