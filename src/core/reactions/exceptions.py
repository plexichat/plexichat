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

    def __init__(self, message: str, permission: str | None = None):
        super().__init__(message)
        self.permission = permission


class UserBlockedError(ReactionError):
    """Cannot interact due to block relationship."""

    pass


class EmojiLimitError(ReactionError):
    """Maximum emoji limit reached for server."""

    def __init__(self, message: str, max_allowed: int, current: int):
        super().__init__(message)
        self.max_allowed = max_allowed
        self.current = current


class EmojiNameExistsError(ReactionError):
    """Custom emoji with this name already exists in the server."""

    pass


class InvalidEmojiNameError(ReactionError):
    """Emoji name is invalid."""

    pass


class EmojiFileSizeError(ReactionError):
    """Emoji file size exceeds limit."""

    def __init__(self, message: str, max_size: int, actual_size: int):
        super().__init__(message)
        self.max_size = max_size
        self.actual_size = actual_size


class InvalidEmojiFileError(ReactionError):
    """Emoji file format is invalid."""

    pass
