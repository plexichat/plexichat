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


def is_trusted_device(user_id: int, fingerprint: str | None) -> bool:
    """True when this (user, fingerprint) device is marked trusted (2FA skip)."""
    return _get_auth_manager().get_instance().is_trusted_device(user_id, fingerprint)


def trust_device(user_id: int, fingerprint: str | None) -> bool:
    """Mark a known device as trusted. Unknown fingerprints are a no-op."""
    return _get_auth_manager().get_instance().trust_device(user_id, fingerprint)


def untrust_device(user_id: int, fingerprint: str | None) -> bool:
    """Remove trusted status from a device."""
    return _get_auth_manager().get_instance().untrust_device(user_id, fingerprint)


__all__ = [
    "get_devices",
    "rename_device",
    "revoke_device",
    "is_trusted_device",
    "trust_device",
    "untrust_device",
]
