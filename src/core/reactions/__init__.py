"""
Reactions module - Zero-friction API for message reactions.

Setup once in main.py, use anywhere via import.

Usage:
    # In main.py (setup once)
    from src.core import reactions
    reactions.setup(db, messaging, servers, relationships)

    # In any other file (use directly)
    from src.core import reactions
    reaction = reactions.add_reaction(user_id=1, message_id=123, emoji="thumbsup")
"""

from typing import Any, Optional, List, Dict

from .models import (
    Reaction,
    ReactionCount,
    ReactionUser,
    MessageReactions,
    CustomEmoji,
)
from .exceptions import (
    ReactionError,
    MessageNotFoundError,
    ReactionNotFoundError,
    ReactionExistsError,
    InvalidEmojiError,
    CustomEmojiNotFoundError,
    ReactionLimitError,
    PermissionDeniedError,
    UserBlockedError,
    EmojiLimitError,
    EmojiNameExistsError,
    InvalidEmojiNameError,
    EmojiFileSizeError,
    InvalidEmojiFileError,
)

__all__ = [
    # Models
    "Reaction",
    "ReactionCount",
    "ReactionUser",
    "MessageReactions",
    "CustomEmoji",
    # Exceptions
    "ReactionError",
    "MessageNotFoundError",
    "ReactionNotFoundError",
    "ReactionExistsError",
    "InvalidEmojiError",
    "CustomEmojiNotFoundError",
    "ReactionLimitError",
    "PermissionDeniedError",
    "UserBlockedError",
    "EmojiLimitError",
    "EmojiNameExistsError",
    "InvalidEmojiNameError",
    "EmojiFileSizeError",
    "InvalidEmojiFileError",
    # Setup
    "setup",
    # Reaction operations
    "add_reaction",
    "remove_reaction",
    "remove_all_reactions",
    "remove_all_reactions_for_emoji",
    "get_reaction",
    "get_reactions",
    "get_reaction_users",
    "get_user_reactions",
    # Custom emoji operations
    "create_custom_emoji",
    "update_custom_emoji",
    "delete_custom_emoji",
    "get_custom_emoji",
    "get_server_custom_emojis",
    "get_emoji_counts",
]

_manager = None
_setup_complete = False


def setup(db: Any, messaging_module: Optional[Any] = None, servers_module: Optional[Any] = None, relationships_module: Optional[Any] = None, media_module: Optional[Any] = None) -> None:
    """
    Initialize the reactions module.

    Args:
        db: Database instance (must be connected)
        messaging_module: Optional messaging module for message access
        servers_module: Optional servers module for permission checks
        relationships_module: Optional relationships module for block filtering
        media_module: Optional media module for emoji image uploads
    """
    global _manager, _setup_complete

    from .manager import ReactionManager

    _manager = ReactionManager(db, messaging_module, servers_module, relationships_module, media_module)
    _setup_complete = True


def _get_manager():
    """Get the manager instance, raising if not setup."""
    if not _setup_complete or _manager is None:
        raise RuntimeError(
            "Reactions module not initialized. Call reactions.setup(db) first."
        )
    return _manager


# === Reaction Operations ===


def add_reaction(user_id: int, message_id: int, emoji: str) -> Reaction:
    """Add a reaction to a message."""
    return _get_manager().add_reaction(user_id, message_id, emoji)


def remove_reaction(user_id: int, message_id: int, emoji: str) -> bool:
    """Remove a reaction from a message."""
    return _get_manager().remove_reaction(user_id, message_id, emoji)


def remove_all_reactions(user_id: int, message_id: int) -> int:
    """Remove all reactions from a message (moderator action)."""
    return _get_manager().remove_all_reactions(user_id, message_id)


def remove_all_reactions_for_emoji(user_id: int, message_id: int, emoji: str) -> int:
    """Remove all reactions of a specific emoji from a message (moderator action)."""
    return _get_manager().remove_all_reactions_for_emoji(user_id, message_id, emoji)


def get_reaction(reaction_id: int) -> Optional[Reaction]:
    """Get a reaction by ID."""
    return _get_manager().get_reaction(reaction_id)


def get_reactions(user_id: int, message_id: int) -> MessageReactions:
    """Get all reactions on a message with counts."""
    return _get_manager().get_reactions(user_id, message_id)


def get_reactions_batch(user_id: int, message_ids: List[int]) -> Dict[int, List[Dict]]:
    """
    Get reactions for multiple messages in a single batch query.
    
    Optimized to avoid N+1 queries when loading message lists.
    Returns dict mapping message_id to list of reaction dicts.
    """
    return _get_manager().get_reactions_batch(user_id, message_ids)


def get_reaction_users(
    user_id: int,
    message_id: int,
    emoji: str,
    limit: int = 100,
    after_user_id: Optional[int] = None
) -> List[ReactionUser]:
    """Get users who reacted with a specific emoji."""
    return _get_manager().get_reaction_users(user_id, message_id, emoji, limit, after_user_id)


def get_user_reactions(user_id: int, message_id: int) -> List[Reaction]:
    """Get all reactions by a specific user on a message."""
    return _get_manager().get_user_reactions(user_id, message_id)


# === Custom Emoji Operations ===


def create_custom_emoji(
    user_id: int,
    server_id: int,
    name: str,
    image_data: bytes,
    content_type: str,
) -> CustomEmoji:
    """Create a custom emoji for a server with image upload."""
    return _get_manager().create_custom_emoji(user_id, server_id, name, image_data, content_type)


def update_custom_emoji(
    user_id: int,
    emoji_id: int,
    name: Optional[str] = None,
) -> CustomEmoji:
    """Update a custom emoji's name."""
    return _get_manager().update_custom_emoji(user_id, emoji_id, name)


def delete_custom_emoji(user_id: int, emoji_id: int) -> bool:
    """Delete a custom emoji."""
    return _get_manager().delete_custom_emoji(user_id, emoji_id)


def get_custom_emoji(emoji_id: int) -> Optional[CustomEmoji]:
    """Get a custom emoji by ID."""
    return _get_manager().get_custom_emoji(emoji_id)


def get_server_custom_emojis(server_id: int, include_unavailable: bool = False) -> List[CustomEmoji]:
    """Get all custom emojis for a server."""
    return _get_manager().get_server_custom_emojis(server_id, include_unavailable)


def get_emoji_counts(server_id: int) -> dict:
    """Get emoji counts for a server."""
    return _get_manager().get_emoji_counts(server_id)
