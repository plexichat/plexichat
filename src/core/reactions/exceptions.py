"""
Reaction exceptions - All reaction-related error types.
"""


class ReactionError(Exception):
    """Base exception for all reaction errors."""
    pass


class MessageNotFoundError(ReactionError):
    """Message does not exist or is not accessible."""
    pass


class ReactionNotFoundError(ReactionError):
    """Reaction does not exist."""
    pass


class ReactionExistsError(ReactionError):
    """User has already reacted with this emoji."""
    pass


class InvalidEmojiError(ReactionError):
    """Emoji is invalid or not allowed."""
    pass


class CustomEmojiNotFoundError(ReactionError):
    """Custom emoji does not exist in the server."""
    pass


class ReactionLimitError(ReactionError):
    """Maximum reactions limit reached."""
    
    def __init__(self, message: str, max_allowed: int, current: int):
        super().__init__(message)
        self.max_allowed = max_allowed
        self.current = current


class PermissionDeniedError(ReactionError):
    """User does not have permission to perform this action."""
    
    def __init__(self, message: str, permission: str = None):
        super().__init__(message)
        self.permission = permission


class UserBlockedError(ReactionError):
    """Cannot interact due to block relationship."""
    pass
