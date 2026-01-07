"""
Presence exceptions - All presence-related error types.
"""


class PresenceError(Exception):
    """Base exception for all presence errors."""

    pass


class UserNotFoundError(PresenceError):
    """User does not exist."""

    pass


class InvalidStatusError(PresenceError):
    """Invalid status value."""

    pass


class InvalidActivityError(PresenceError):
    """Invalid activity data."""

    pass


class TypingIndicatorError(PresenceError):
    """Error with typing indicator."""

    pass


class PresenceNotFoundError(PresenceError):
    """Presence record not found."""

    pass
