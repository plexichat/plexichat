"""
Notification exceptions - All notification-related error types.
"""


class NotificationError(Exception):
    """Base exception for all notification errors."""
    pass


class UserNotFoundError(NotificationError):
    """User does not exist."""
    pass


class MessageNotFoundError(NotificationError):
    """Message does not exist or is not accessible."""
    pass


class ChannelNotFoundError(NotificationError):
    """Channel does not exist."""
    pass


class ServerNotFoundError(NotificationError):
    """Server does not exist."""
    pass


class InvalidMentionError(NotificationError):
    """Mention is invalid or malformed."""
    pass


class PermissionDeniedError(NotificationError):
    """User does not have permission to perform this action."""

    def __init__(self, message: str, permission: str | None = None):
        super().__init__(message)
        self.permission = permission


class NotificationNotFoundError(NotificationError):
    """Notification does not exist."""
    pass


class SettingsNotFoundError(NotificationError):
    """Settings not found."""
    pass
