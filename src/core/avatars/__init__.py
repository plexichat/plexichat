"""
Avatars Module - Manages user and server avatars stored in database.

This module provides:
- Avatar upload with automatic resizing
- Database BLOB storage for avatars
- Configurable max dimensions
- Support for user avatars and server icons
- Animated GIF support (optional)

Usage:
    from src.core import avatars
    avatars.setup(db)

    # Upload user avatar
    avatar = avatars.upload_user_avatar(user_id, image_bytes, "image/png")

    # Get avatar URL
    url = avatars.get_user_avatar_url(user_id)

    # Get avatar bytes for serving
    data, content_type = avatars.get_avatar_data(avatar_id)
"""

from typing import Optional, Tuple, Dict, Any

from .manager import AvatarManager
from .schema import create_tables as _create_tables_impl

__all__ = [
    "setup",
    "is_setup",
    "create_tables",
    "get_user_avatar_checksum",
    "get_user_avatar",
    "get_user_avatar_data",
    "get_user_avatar_url",
    "delete_user_avatar",
    "upload_user_avatar",
    "upload_server_icon",
    "get_server_icon",
    "get_server_icon_data",
    "get_server_icon_url",
    "get_server_icon_checksum",
    "delete_server_icon",
    "generate_default_svg",
]

_manager: Optional[AvatarManager] = None


def setup(db: Any) -> None:
    """Initialize the avatars module."""
    global _manager
    _manager = AvatarManager(db=db)
    _manager.setup(db)


def _get_manager() -> AvatarManager:
    """Get the manager instance, raising if not setup."""
    if _manager is None or not _manager.is_setup():
        raise RuntimeError(
            "Avatars module not initialized. Call avatars.setup(db) first."
        )
    return _manager


def is_setup() -> bool:
    """Check if module is initialized."""
    return _manager is not None and _manager.is_setup()


def create_tables(db: Any) -> None:
    """Create avatar tables. Safe to call before module setup.

    The DDL lives in :mod:`src.core.avatars.schema` and is invoked directly
    here so migration 000 (which runs before ``avatars.setup``) can populate
    the tables. Once the manager is set up, we still delegate to the manager
    for consistency with hot-reload paths.
    """
    if _manager is not None and _manager.is_setup():
        try:
            _manager.create_tables(db)
            return
        except Exception:
            # Manager path failed (e.g. test environment) - fall back to
            # the standalone DDL.
            pass
    _create_tables_impl(db)


# === User Avatars ===


def get_user_avatar_checksum(user_id: int) -> Optional[str]:
    """Get avatar checksum for ETag (cached)."""
    return _get_manager().get_user_avatar_checksum(user_id)


def upload_user_avatar(
    user_id: int, image_data: bytes, content_type: str
) -> Dict[str, Any]:
    """Upload or update a user's avatar."""
    return _get_manager().upload_user_avatar(user_id, image_data, content_type)


def get_user_avatar(user_id: int) -> Optional[Dict[str, Any]]:
    """Get user avatar metadata."""
    return _get_manager().get_user_avatar(user_id)


def get_user_avatar_data(user_id: int) -> Optional[Tuple[bytes, str, str]]:
    """Get user avatar binary data (cached)."""
    return _get_manager().get_user_avatar_data(user_id)


def get_user_avatar_url(user_id: int) -> Optional[str]:
    """Get user avatar URL if exists."""
    return _get_manager().get_user_avatar_url(user_id)


def delete_user_avatar(user_id: int) -> bool:
    """Delete user avatar."""
    return _get_manager().delete_user_avatar(user_id)


# === Server Icons ===


def upload_server_icon(
    server_id: int, image_data: bytes, content_type: str
) -> Dict[str, Any]:
    """Upload or update a server's icon."""
    return _get_manager().upload_server_icon(server_id, image_data, content_type)


def get_server_icon(server_id: int) -> Optional[Dict[str, Any]]:
    """Get server icon metadata."""
    return _get_manager().get_server_icon(server_id)


def get_server_icon_data(server_id: int) -> Optional[Tuple[bytes, str, str]]:
    """Get server icon binary data (cached)."""
    return _get_manager().get_server_icon_data(server_id)


def get_server_icon_url(server_id: int) -> Optional[str]:
    """Get server icon URL if exists."""
    return _get_manager().get_server_icon_url(server_id)


def get_server_icon_checksum(server_id: int) -> Optional[str]:
    """Get server icon checksum for ETag (cached)."""
    return _get_manager().get_server_icon_checksum(server_id)


def delete_server_icon(server_id: int) -> bool:
    """Delete server icon."""
    return _get_manager().delete_server_icon(server_id)


# === Default SVG Placeholder ===


def generate_default_svg(seed: Any, initials: str) -> str:
    """Generate a colorful SVG placeholder avatar."""
    return _get_manager().generate_default_svg(seed, initials)
