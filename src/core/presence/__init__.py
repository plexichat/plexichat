"""
Presence module - Zero-friction API for user presence and status management.

Setup once in main.py, use anywhere via import.

Usage:
    # In main.py (setup once)
    from src.core import presence
    presence.setup(db, auth, relationships, servers)

    # In any other file (use directly)
    from src.core import presence
    presence.set_status(user_id=1, status=presence.UserStatus.ONLINE)
"""

from typing import Any, Optional, List, Dict

from .models import (
    Presence,
    UserStatus,
    Activity,
    ActivityType,
    TypingIndicator,
    CustomStatus,
)
from .exceptions import (
    PresenceError,
    UserNotFoundError,
    InvalidStatusError,
    InvalidActivityError,
    TypingIndicatorError,
    PresenceNotFoundError,
)

__all__ = [
    # Models
    "Presence",
    "UserStatus",
    "Activity",
    "ActivityType",
    "TypingIndicator",
    "CustomStatus",
    # Exceptions
    "PresenceError",
    "UserNotFoundError",
    "InvalidStatusError",
    "InvalidActivityError",
    "TypingIndicatorError",
    "PresenceNotFoundError",
    # Setup
    "setup",
    # Status operations
    "set_status",
    "get_status",
    "clear_status",
    # Custom status operations
    "set_custom_status",
    "get_custom_status",
    "clear_custom_status",
    # Activity operations
    "set_activity",
    "get_activity",
    "clear_activity",
    # Presence operations
    "get_presence",
    "get_presences",
    "update_last_seen",
    "set_focused_channel",
    # Typing indicators
    "start_typing",
    "stop_typing",
    "get_typing_users",
    # Online queries
    "get_online_friends",
    "get_online_server_members",
    # Visibility
    "get_visible_presence",
    "get_visible_presences_bulk",
    "can_see_presence",
]

_manager = None
_setup_complete = False


def setup(
    db: Any,
    auth_module: Optional[Any] = None,
    relationships_module: Optional[Any] = None,
    servers_module: Optional[Any] = None,
) -> None:
    """
    Initialize the presence module.

    Args:
        db: Database instance (must be connected)
        auth_module: Optional auth module for user verification
        relationships_module: Optional relationships module for friend queries
        servers_module: Optional servers module for server member queries
    """
    global _manager, _setup_complete

    from .manager import PresenceManager

    _manager = PresenceManager(db, auth_module, relationships_module, servers_module)
    _setup_complete = True


def _get_manager():
    """Get the manager instance, raising if not setup."""
    if not _setup_complete or _manager is None:
        raise RuntimeError(
            "Presence module not initialized. Call presence.setup(db) first."
        )
    return _manager


# === Status Operations ===


def set_status(user_id: int, status: UserStatus) -> Presence:
    """Set user's online status."""
    return _get_manager().set_status(user_id, status)


def get_status(user_id: int) -> UserStatus:
    """Get user's current status."""
    return _get_manager().get_status(user_id)


def clear_status(user_id: int) -> Presence:
    """Clear user's status (set to offline)."""
    return _get_manager().clear_status(user_id)


# === Custom Status Operations ===


def set_custom_status(
    user_id: int,
    text: str,
    emoji: Optional[str] = None,
    expires_at: Optional[int] = None,
) -> Presence:
    """Set user's custom status message."""
    return _get_manager().set_custom_status(user_id, text, emoji, expires_at)


def get_custom_status(user_id: int) -> Optional[CustomStatus]:
    """Get user's custom status."""
    return _get_manager().get_custom_status(user_id)


def clear_custom_status(user_id: int) -> Presence:
    """Clear user's custom status."""
    return _get_manager().clear_custom_status(user_id)


# === Activity Operations ===


def set_activity(
    user_id: int,
    activity_type: ActivityType,
    name: str,
    details: Optional[str] = None,
    url: Optional[str] = None,
    state: Optional[str] = None,
    timestamps: Optional[Dict[str, int]] = None,
    assets: Optional[Dict[str, str]] = None,
) -> Presence:
    """Set user's current activity."""
    return _get_manager().set_activity(
        user_id, activity_type, name, details, url, state, timestamps, assets
    )


def get_activity(user_id: int) -> Optional[Activity]:
    """Get user's current activity."""
    return _get_manager().get_activity(user_id)


def clear_activity(user_id: int) -> Presence:
    """Clear user's current activity."""
    return _get_manager().clear_activity(user_id)


# === Presence Operations ===


def get_presence(user_id: int) -> Presence:
    """Get full presence information for a user."""
    return _get_manager().get_presence(user_id)


def get_presences(user_ids: List[int]) -> List[Presence]:
    """Get presence information for multiple users."""
    return _get_manager().get_presences(user_ids)


def update_last_seen(user_id: int) -> Presence:
    """Update user's last seen timestamp."""
    return _get_manager().update_last_seen(user_id)


def set_focused_channel(
    user_id: int,
    channel_id: Optional[int] = None,
    server_id: Optional[int] = None,
) -> bool:
    """Set user's focused channel/server in transient presence."""
    return _get_manager().set_focused_channel(user_id, channel_id, server_id)


# === Typing Indicators ===


def start_typing(user_id: int, channel_id: int) -> TypingIndicator:
    """Start typing indicator in a channel."""
    return _get_manager().start_typing(user_id, channel_id)


def stop_typing(user_id: int, channel_id: int) -> bool:
    """Stop typing indicator in a channel."""
    return _get_manager().stop_typing(user_id, channel_id)


def get_typing_users(channel_id: int) -> List[TypingIndicator]:
    """Get users currently typing in a channel."""
    return _get_manager().get_typing_users(channel_id)


# === Online Queries ===


def get_online_friends(user_id: int) -> List[int]:
    """Get list of online friend user IDs."""
    return _get_manager().get_online_friends(user_id)


def get_online_server_members(user_id: int, server_id: int) -> List[int]:
    """Get list of online member user IDs in a server."""
    return _get_manager().get_online_server_members(user_id, server_id)


# === Visibility ===


def get_visible_presence(viewer_id: int, target_id: int) -> Presence:
    """Get presence as visible to a specific viewer (respects invisible mode and blocks)."""
    return _get_manager().get_visible_presence(viewer_id, target_id)


def get_visible_presences_bulk(
    viewer_id: int, target_ids: List[int]
) -> Dict[int, Presence]:
    """Get multiple presences as visible to a specific viewer."""
    return _get_manager().get_visible_presences_bulk(viewer_id, target_ids)


def can_see_presence(viewer_id: int, target_id: int) -> bool:
    """Check if viewer can see target's real presence."""
    return _get_manager().can_see_presence(viewer_id, target_id)
