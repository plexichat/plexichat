"""
Stickers module - Zero-friction API for sticker packs and custom stickers.

Setup once in main.py, use anywhere via import.

Usage:
    # In main.py (setup once)
    from src.core import stickers
    stickers.setup(db, messaging, servers)

    # In any other file (use directly)
    from src.core import stickers
    pack = stickers.create_pack(user_id, "My Pack", server_id=server_id)
"""

from typing import Optional, List

from .models import (
    StickerPack,
    Sticker,
    StickerUsage,
    StickerSuggestion,
    StickerFormat,
    PackType,
)
from .exceptions import (
    StickerError,
    PackNotFoundError,
    StickerNotFoundError,
    PackLimitError,
    StickerLimitError,
    InvalidStickerFormatError,
    StickerTooLargeError,
    InvalidStickerNameError,
    InvalidPackNameError,
    PermissionDeniedError,
    ServerNotFoundError,
    MessageNotFoundError,
)

__all__ = [
    "StickerPack",
    "Sticker",
    "StickerUsage",
    "StickerSuggestion",
    "StickerFormat",
    "PackType",
    "StickerError",
    "PackNotFoundError",
    "StickerNotFoundError",
    "PackLimitError",
    "StickerLimitError",
    "InvalidStickerFormatError",
    "StickerTooLargeError",
    "InvalidStickerNameError",
    "InvalidPackNameError",
    "PermissionDeniedError",
    "ServerNotFoundError",
    "MessageNotFoundError",
    "setup",
    "create_pack",
    "get_pack",
    "get_server_packs",
    "delete_pack",
    "add_sticker",
    "get_sticker",
    "get_pack_stickers",
    "remove_sticker",
    "send_sticker",
    "get_sticker_suggestions",
]

_manager = None
_setup_complete = False


def setup(db, messaging_module=None, servers_module=None):
    """
    Initialize the stickers module.

    Args:
        db: Database instance (must be connected)
        messaging_module: Optional messaging module for message access
        servers_module: Optional servers module for permission checks
    """
    global _manager, _setup_complete

    from .manager import StickerManager

    _manager = StickerManager(db, messaging_module, servers_module)
    _setup_complete = True


def _get_manager():
    """Get the manager instance, raising if not setup."""
    if not _setup_complete or _manager is None:
        raise RuntimeError(
            "Stickers module not initialized. Call stickers.setup(db) first."
        )
    return _manager


def create_pack(
    user_id: int,
    name: str,
    description: Optional[str] = None,
    server_id: Optional[int] = None,
    pack_type: PackType = PackType.SERVER,
) -> StickerPack:
    """Create a new sticker pack."""
    return _get_manager().create_pack(user_id, name, description, server_id, pack_type)


def get_pack(pack_id: int, user_id: int) -> Optional[StickerPack]:
    """Get a sticker pack by ID."""
    return _get_manager().get_pack(pack_id, user_id)


def get_server_packs(user_id: int, server_id: int) -> List[StickerPack]:
    """Get all sticker packs for a server."""
    return _get_manager().get_server_packs(user_id, server_id)


def delete_pack(user_id: int, pack_id: int) -> bool:
    """Delete a sticker pack."""
    return _get_manager().delete_pack(user_id, pack_id)


def add_sticker(
    user_id: int,
    pack_id: int,
    name: str,
    format: StickerFormat,
    url: str,
    size: int,
    tags: Optional[List[str]] = None,
    related_emoji: Optional[str] = None,
    width: Optional[int] = None,
    height: Optional[int] = None,
) -> Sticker:
    """Add a sticker to a pack."""
    return _get_manager().add_sticker(
        user_id, pack_id, name, format, url, size, tags, related_emoji, width, height
    )


def get_sticker(sticker_id: int) -> Optional[Sticker]:
    """Get a sticker by ID."""
    return _get_manager().get_sticker(sticker_id)


def get_pack_stickers(user_id: int, pack_id: int) -> List[Sticker]:
    """Get all stickers in a pack."""
    return _get_manager().get_pack_stickers(user_id, pack_id)


def remove_sticker(user_id: int, sticker_id: int) -> bool:
    """Remove a sticker from its pack."""
    return _get_manager().remove_sticker(user_id, sticker_id)


def send_sticker(user_id: int, message_id: int, sticker_id: int) -> StickerUsage:
    """Send a sticker in a message (track usage)."""
    return _get_manager().send_sticker(user_id, message_id, sticker_id)


def get_sticker_suggestions(
    user_id: int,
    content: str,
    server_id: Optional[int] = None,
    limit: Optional[int] = None
) -> List[StickerSuggestion]:
    """Get sticker suggestions based on message content."""
    return _get_manager().get_sticker_suggestions(user_id, content, server_id, limit)
