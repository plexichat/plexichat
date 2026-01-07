"""
Messaging exceptions.

All messaging-related errors inherit from MessagingError for easy catching.
"""


class MessagingError(Exception):
    """Base exception for all messaging errors."""

    pass


class ConversationNotFoundError(MessagingError):
    """Raised when a conversation cannot be found."""

    pass


class ConversationAccessDeniedError(MessagingError):
    """Raised when user does not have access to a conversation."""

    def __init__(self, message: str, required_permission: str | None = None):
        super().__init__(message)
        self.required_permission = required_permission


class MessageNotFoundError(MessagingError):
    """Raised when a message cannot be found."""

    pass


class MessageAccessDeniedError(MessagingError):
    """Raised when user does not have access to a message."""

    def __init__(self, message: str, required_permission: str | None = None):
        super().__init__(message)
        self.required_permission = required_permission


class ParticipantNotFoundError(MessagingError):
    """Raised when a participant cannot be found in a conversation."""

    pass


class ParticipantExistsError(MessagingError):
    """Raised when trying to add a participant that already exists."""

    pass


class ParticipantLimitError(MessagingError):
    """Raised when conversation participant limit is reached."""

    def __init__(self, message: str, limit: int, current: int):
        super().__init__(message)
        self.limit = limit
        self.current = current


class InvalidContentError(MessagingError):
    """Raised when message content fails validation."""

    def __init__(self, message: str, issues: list | None = None):
        super().__init__(message)
        self.issues = issues or []


class ContentTooLongError(MessagingError):
    """Raised when message content exceeds maximum length."""

    def __init__(self, message: str, max_length: int, actual_length: int):
        super().__init__(message)
        self.max_length = max_length
        self.actual_length = actual_length


class AttachmentError(MessagingError):
    """Raised when there is an issue with an attachment."""

    pass


class AttachmentTooLargeError(AttachmentError):
    """Raised when attachment exceeds size limit."""

    def __init__(self, message: str, max_size: int, actual_size: int):
        super().__init__(message)
        self.max_size = max_size
        self.actual_size = actual_size


class AttachmentLimitError(AttachmentError):
    """Raised when attachment count limit is exceeded."""

    def __init__(self, message: str, max_count: int, actual_count: int):
        super().__init__(message)
        self.max_count = max_count
        self.actual_count = actual_count


class RateLimitError(MessagingError):
    """Raised when user exceeds rate limits."""

    def __init__(self, message: str, retry_after: int | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class InvalidRecipientError(MessagingError):
    """Raised when recipient is invalid for messaging."""

    pass


class ConversationTypeError(MessagingError):
    """Raised when operation is invalid for conversation type."""

    pass


class PinLimitError(MessagingError):
    """Raised when pin limit is reached."""

    def __init__(self, message: str, limit: int, current: int):
        super().__init__(message)
        self.limit = limit
        self.current = current


class MessageNotPinnedError(MessagingError):
    """Raised when trying to unpin a message that is not pinned."""

    pass


class AttachmentNotFoundError(AttachmentError):
    """Raised when an attachment cannot be found."""

    pass
