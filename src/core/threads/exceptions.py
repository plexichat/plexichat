"""
Thread exceptions - All thread-related error types.
"""


class ThreadError(Exception):
    """Base exception for all thread errors."""
    pass


class ThreadNotFoundError(ThreadError):
    """Thread does not exist."""
    pass


class ThreadAccessDeniedError(ThreadError):
    """User does not have permission to access this thread."""

    def __init__(self, message: str = "Access denied"):
        super().__init__(message)


class ThreadArchivedError(ThreadError):
    """Thread is archived and cannot be modified."""
    pass


class ThreadLockedError(ThreadError):
    """Thread is locked and cannot receive new messages."""
    pass


class ThreadMemberNotFoundError(ThreadError):
    """User is not a member of the thread."""
    pass


class ThreadMemberExistsError(ThreadError):
    """User is already a member of the thread."""
    pass


class ThreadNameError(ThreadError):
    """Invalid thread name."""

    def __init__(self, message: str, name: str = ""):
        super().__init__(message)
        self.name = name


class MessageNotFoundError(ThreadError):
    """Message does not exist."""
    pass


class ChannelNotFoundError(ThreadError):
    """Channel does not exist."""
    pass


class PermissionDeniedError(ThreadError):
    """User does not have the required permission."""

    def __init__(self, message: str, permission: str = ""):
        super().__init__(message)
        self.permission = permission


class InvalidThreadTypeError(ThreadError):
    """Invalid thread type for this operation."""

    def __init__(self, message: str, expected: str = "", actual: str = ""):
        super().__init__(message)
        self.expected = expected
        self.actual = actual
