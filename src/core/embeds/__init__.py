"""
Embeds module - Zero-friction API for rich embeds on messages.

Setup once in main.py, use anywhere via import.

Usage:
    # In main.py (setup once)
    from src.core import embeds
    embeds.setup(db, messaging, servers)

    # In any other file (use directly)
    from src.core import embeds
    embed = embeds.create_embed(user_id=1, title="Hello", description="World")
"""

from typing import Optional, List, Dict, Any

from .models import (
    Embed,
    EmbedField,
    EmbedAuthor,
    EmbedFooter,
    EmbedImage,
    EmbedThumbnail,
    EmbedProvider,
    EmbedType,
)
from .exceptions import (
    EmbedError,
    EmbedNotFoundError,
    EmbedValidationError,
    EmbedLimitError,
    EmbedFieldLimitError,
    EmbedCharacterLimitError,
    InvalidUrlError,
    InvalidColorError,
    MessageNotFoundError,
    PermissionDeniedError,
    EmbedSanitizationError,
)

__all__ = [
    # Models
    "Embed",
    "EmbedField",
    "EmbedAuthor",
    "EmbedFooter",
    "EmbedImage",
    "EmbedThumbnail",
    "EmbedProvider",
    "EmbedType",
    # Exceptions
    "EmbedError",
    "EmbedNotFoundError",
    "EmbedValidationError",
    "EmbedLimitError",
    "EmbedFieldLimitError",
    "EmbedCharacterLimitError",
    "InvalidUrlError",
    "InvalidColorError",
    "MessageNotFoundError",
    "PermissionDeniedError",
    "EmbedSanitizationError",
    # Setup
    "setup",
    # Embed operations
    "create_embed",
    "get_embed",
    "update_embed",
    "delete_embed",
    "attach_embed_to_message",
    "remove_embed_from_message",
    "get_message_embeds",
    "suppress_embeds",
    "unsuppress_embeds",
    # URL preview operations
    "create_url_preview",
    "parse_url_metadata",
    # Validation
    "validate_embed",
    "sanitize_embed_content",
]

_manager = None
_setup_complete = False


def setup(db: Any, messaging_module: Optional[Any] = None, servers_module: Optional[Any] = None) -> None:
    """
    Initialize the embeds module.

    Args:
        db: Database instance (must be connected)
        messaging_module: Optional messaging module for message access
        servers_module: Optional servers module for permission checks
    """
    global _manager, _setup_complete

    from .manager import EmbedManager

    _manager = EmbedManager(db, messaging_module, servers_module)
    _setup_complete = True


def _get_manager():
    """Get the manager instance, raising if not setup."""
    if not _setup_complete or _manager is None:
        raise RuntimeError(
            "Embeds module not initialized. Call embeds.setup(db) first."
        )
    return _manager


# === Embed Operations ===


def create_embed(
    user_id: int,
    title: Optional[str] = None,
    description: Optional[str] = None,
    url: Optional[str] = None,
    timestamp: Optional[str] = None,
    color: Optional[str] = None,
    footer: Optional[Dict[str, Any]] = None,
    image: Optional[Dict[str, Any]] = None,
    thumbnail: Optional[Dict[str, Any]] = None,
    author: Optional[Dict[str, Any]] = None,
    fields: Optional[List[Dict[str, Any]]] = None,
    provider: Optional[Dict[str, Any]] = None,
    embed_type: EmbedType = EmbedType.RICH
) -> Embed:
    """Create a new embed."""
    return _get_manager().create_embed(
        user_id=user_id,
        title=title,
        description=description,
        url=url,
        timestamp=timestamp,
        color=color,
        footer=footer,
        image=image,
        thumbnail=thumbnail,
        author=author,
        fields=fields,
        provider=provider,
        embed_type=embed_type
    )


def get_embed(embed_id: int) -> Optional[Embed]:
    """Get an embed by ID."""
    return _get_manager().get_embed(embed_id)


def update_embed(
    user_id: int,
    embed_id: int,
    title: Optional[str] = None,
    description: Optional[str] = None,
    url: Optional[str] = None,
    timestamp: Optional[str] = None,
    color: Optional[str] = None,
    footer: Optional[Dict[str, Any]] = None,
    image: Optional[Dict[str, Any]] = None,
    thumbnail: Optional[Dict[str, Any]] = None,
    author: Optional[Dict[str, Any]] = None,
    fields: Optional[List[Dict[str, Any]]] = None
) -> Embed:
    """Update an existing embed."""
    return _get_manager().update_embed(
        user_id=user_id,
        embed_id=embed_id,
        title=title,
        description=description,
        url=url,
        timestamp=timestamp,
        color=color,
        footer=footer,
        image=image,
        thumbnail=thumbnail,
        author=author,
        fields=fields
    )


def delete_embed(user_id: int, embed_id: int) -> bool:
    """Delete an embed."""
    return _get_manager().delete_embed(user_id, embed_id)


def attach_embed_to_message(
    user_id: int,
    message_id: int,
    embed_id: int,
    position: Optional[int] = None
) -> bool:
    """Attach an embed to a message."""
    return _get_manager().attach_embed_to_message(user_id, message_id, embed_id, position)


def remove_embed_from_message(user_id: int, message_id: int, embed_id: int) -> bool:
    """Remove an embed from a message."""
    return _get_manager().remove_embed_from_message(user_id, message_id, embed_id)


def get_message_embeds(user_id: int, message_id: int) -> List[Embed]:
    """Get all embeds attached to a message."""
    return _get_manager().get_message_embeds(user_id, message_id)


def suppress_embeds(user_id: int, message_id: int) -> bool:
    """Suppress (hide) all embeds on a message."""
    return _get_manager().suppress_embeds(user_id, message_id)


def unsuppress_embeds(user_id: int, message_id: int) -> bool:
    """Unsuppress (show) all embeds on a message."""
    return _get_manager().unsuppress_embeds(user_id, message_id)


# === URL Preview Operations ===


def create_url_preview(
    user_id: int,
    url: str,
    message_id: Optional[int] = None
) -> Embed:
    """Create a URL preview embed from OpenGraph/Twitter Card metadata."""
    return _get_manager().create_url_preview(user_id, url, message_id)


def parse_url_metadata(url: str) -> Dict[str, Any]:
    """Parse URL metadata (OpenGraph/Twitter Card) without creating embed."""
    return _get_manager().parse_url_metadata(url)


# === Validation ===


def validate_embed(embed_data: Dict[str, Any]) -> Dict[str, Any]:
    """Validate embed data and return validation result."""
    return _get_manager().validate_embed_data(embed_data)


def sanitize_embed_content(content: str) -> str:
    """Sanitize embed content (remove scripts, validate URLs)."""
    return _get_manager().sanitize_content(content)
