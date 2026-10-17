"""
Bot account management module.

This module handles bot creation, retrieval, permission updates, token regeneration,
enabling/disabling, and deletion.
"""

from typing import Optional, Dict, List
from .models import Bot
from ._lazy import _get_auth_manager


def create_bot(
    owner_id: int,
    username: str,
    display_name: str,
    permissions: Optional[Dict[str, bool]] = None,
) -> Bot:
    """
    Create a bot account.

    Args:
        owner_id: User ID of the bot owner
        username: Unique username for the bot
        display_name: Display name
        permissions: Optional custom permissions (defaults applied if None)

    Returns:
        Bot object with token (token only returned on creation)
    """
    return (
        _get_auth_manager()
        .get_instance()
        .create_bot(owner_id, username, display_name, permissions)
    )


def get_bot(bot_id: int) -> Optional[Bot]:
    """Get a bot by ID."""
    return _get_auth_manager().get_instance().get_bot(bot_id)


def get_user_bots(owner_id: int) -> List[Bot]:
    """Get all bots owned by a user."""
    return _get_auth_manager().get_instance().get_user_bots(owner_id)


def regenerate_bot_token(owner_id: int, bot_id: int) -> str:
    """Regenerate bot token. Old token immediately invalid."""
    return _get_auth_manager().get_instance().regenerate_bot_token(owner_id, bot_id)


def update_bot_permissions(
    owner_id: int, bot_id: int, permissions: Dict[str, bool]
) -> Bot:
    """Update bot permissions."""
    return (
        _get_auth_manager()
        .get_instance()
        .update_bot_permissions(owner_id, bot_id, permissions)
    )


def disable_bot(owner_id: int, bot_id: int) -> bool:
    """Disable a bot (can be re-enabled)."""
    return _get_auth_manager().get_instance().disable_bot(owner_id, bot_id)


def enable_bot(owner_id: int, bot_id: int) -> bool:
    """Re-enable a disabled bot."""
    return _get_auth_manager().get_instance().enable_bot(owner_id, bot_id)


def delete_bot(owner_id: int, bot_id: int) -> bool:
    """Permanently delete a bot."""
    return _get_auth_manager().get_instance().delete_bot(owner_id, bot_id)


__all__ = [
    "create_bot",
    "get_bot",
    "get_user_bots",
    "regenerate_bot_token",
    "update_bot_permissions",
    "disable_bot",
    "enable_bot",
    "delete_bot",
]
