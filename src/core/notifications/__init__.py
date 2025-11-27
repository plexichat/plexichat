"""
Notifications module - Zero-friction API for mentions and notifications.

Setup once in main.py, use anywhere via import.

Usage:
    # In main.py (setup once)
    from src.core import notifications
    notifications.setup(db, messaging, servers, relationships, presence)

    # In any other file (use directly)
    from src.core import notifications
    mentions = notifications.parse_mentions(content)
    notifications.create_notification(user_id, message_id, mention_type)
"""

from typing import Optional, List, Dict, Any

from .models import (
    Mention,
    MentionType,
    Notification,
    NotificationSettings,
    ChannelNotificationOverride,
    NotificationLevel,
    UnreadCount,
    NotificationFeed,
    MentionPosition,
    PushPayload,
)
from .exceptions import (
    NotificationError,
    UserNotFoundError,
    MessageNotFoundError,
    ChannelNotFoundError,
    ServerNotFoundError,
    InvalidMentionError,
    PermissionDeniedError,
    NotificationNotFoundError,
    SettingsNotFoundError,
)

__all__ = [
    # Models
    "Mention",
    "MentionType",
    "Notification",
    "NotificationSettings",
    "ChannelNotificationOverride",
    "NotificationLevel",
    "UnreadCount",
    "NotificationFeed",
    "MentionPosition",
    "PushPayload",
    # Exceptions
    "NotificationError",
    "UserNotFoundError",
    "MessageNotFoundError",
    "ChannelNotFoundError",
    "ServerNotFoundError",
    "InvalidMentionError",
    "PermissionDeniedError",
    "NotificationNotFoundError",
    "SettingsNotFoundError",
    # Setup
    "setup",
    # Mention parsing
    "parse_mentions",
    "validate_mentions",
    "highlight_mentions",
    # Notification operations
    "create_notifications_for_message",
    "get_notification",
    "get_notifications",
    "mark_notification_read",
    "mark_all_read",
    "mark_channel_read",
    "mark_server_read",
    "delete_notification",
    # Unread counts
    "get_unread_count",
    "get_unread_counts",
    "get_mention_count",
    # Notification feed
    "get_notification_feed",
    # Settings operations
    "get_notification_settings",
    "update_notification_settings",
    "get_channel_override",
    "set_channel_override",
    "delete_channel_override",
    # Push notification hooks
    "prepare_push_payload",
]

_manager = None
_setup_complete = False


def setup(db, messaging_module=None, servers_module=None, relationships_module=None, presence_module=None):
    """
    Initialize the notifications module.

    Args:
        db: Database instance (must be connected)
        messaging_module: Optional messaging module for message access
        servers_module: Optional servers module for role/permission checks
        relationships_module: Optional relationships module for block filtering
        presence_module: Optional presence module for @here functionality
    """
    global _manager, _setup_complete

    from .manager import NotificationManager

    _manager = NotificationManager(db, messaging_module, servers_module, relationships_module, presence_module)
    _setup_complete = True


def _get_manager():
    """Get the manager instance, raising if not setup."""
    if not _setup_complete or _manager is None:
        raise RuntimeError(
            "Notifications module not initialized. Call notifications.setup(db) first."
        )
    return _manager


# === Mention Parsing ===


def parse_mentions(content: str) -> List[Mention]:
    """Parse all mentions from message content."""
    return _get_manager().parse_mentions(content)


def validate_mentions(
    user_id: int,
    mentions: List[Mention],
    server_id: Optional[int] = None,
    channel_id: Optional[int] = None
) -> List[Mention]:
    """Validate mentions and filter out invalid ones."""
    return _get_manager().validate_mentions(user_id, mentions, server_id, channel_id)


def highlight_mentions(content: str, user_id: int) -> List[MentionPosition]:
    """Get positions of mentions relevant to a user for highlighting."""
    return _get_manager().highlight_mentions(content, user_id)


# === Notification Operations ===


def create_notifications_for_message(
    sender_id: int,
    message_id: int,
    conversation_id: int,
    content: str,
    server_id: Optional[int] = None,
    channel_id: Optional[int] = None
) -> List[Notification]:
    """Create notifications for all mentioned users in a message."""
    return _get_manager().create_notifications_for_message(
        sender_id, message_id, conversation_id, content, server_id, channel_id
    )


def get_notification(notification_id: int) -> Optional[Notification]:
    """Get a notification by ID."""
    return _get_manager().get_notification(notification_id)


def get_notifications(
    user_id: int,
    limit: int = 50,
    before_id: Optional[int] = None,
    unread_only: bool = False
) -> List[Notification]:
    """Get notifications for a user."""
    return _get_manager().get_notifications(user_id, limit, before_id, unread_only)


def mark_notification_read(user_id: int, notification_id: int) -> bool:
    """Mark a notification as read."""
    return _get_manager().mark_notification_read(user_id, notification_id)


def mark_all_read(user_id: int) -> int:
    """Mark all notifications as read for a user."""
    return _get_manager().mark_all_read(user_id)


def mark_channel_read(user_id: int, channel_id: int) -> int:
    """Mark all notifications in a channel as read."""
    return _get_manager().mark_channel_read(user_id, channel_id)


def mark_server_read(user_id: int, server_id: int) -> int:
    """Mark all notifications in a server as read."""
    return _get_manager().mark_server_read(user_id, server_id)


def delete_notification(user_id: int, notification_id: int) -> bool:
    """Delete a notification."""
    return _get_manager().delete_notification(user_id, notification_id)


# === Unread Counts ===


def get_unread_count(user_id: int, server_id: Optional[int] = None) -> UnreadCount:
    """Get unread count for a user, optionally filtered by server."""
    return _get_manager().get_unread_count(user_id, server_id)


def get_unread_counts(user_id: int) -> Dict[int, UnreadCount]:
    """Get unread counts per server/conversation for a user."""
    return _get_manager().get_unread_counts(user_id)


def get_mention_count(user_id: int, server_id: Optional[int] = None) -> int:
    """Get count of unread mentions for a user."""
    return _get_manager().get_mention_count(user_id, server_id)


# === Notification Feed ===


def get_notification_feed(
    user_id: int,
    limit: int = 50,
    before_id: Optional[int] = None
) -> NotificationFeed:
    """Get recent mentions across all servers."""
    return _get_manager().get_notification_feed(user_id, limit, before_id)


# === Settings Operations ===


def get_notification_settings(user_id: int, server_id: Optional[int] = None) -> NotificationSettings:
    """Get notification settings for a user."""
    return _get_manager().get_notification_settings(user_id, server_id)


def update_notification_settings(
    user_id: int,
    server_id: Optional[int] = None,
    **kwargs
) -> NotificationSettings:
    """Update notification settings for a user."""
    return _get_manager().update_notification_settings(user_id, server_id, **kwargs)


def get_channel_override(user_id: int, channel_id: int) -> Optional[ChannelNotificationOverride]:
    """Get channel notification override for a user."""
    return _get_manager().get_channel_override(user_id, channel_id)


def set_channel_override(
    user_id: int,
    channel_id: int,
    level: NotificationLevel,
    muted_until: Optional[int] = None
) -> ChannelNotificationOverride:
    """Set channel notification override for a user."""
    return _get_manager().set_channel_override(user_id, channel_id, level, muted_until)


def delete_channel_override(user_id: int, channel_id: int) -> bool:
    """Delete channel notification override."""
    return _get_manager().delete_channel_override(user_id, channel_id)


# === Push Notification Hooks ===


def prepare_push_payload(notification: Notification) -> PushPayload:
    """Prepare push notification payload (does not send)."""
    return _get_manager().prepare_push_payload(notification)
