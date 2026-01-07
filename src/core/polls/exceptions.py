"""
Poll exceptions - All poll-related error types.
"""


class PollError(Exception):
    """Base exception for all poll errors."""

    pass


class PollNotFoundError(PollError):
    """Poll does not exist."""

    pass


class PollOptionNotFoundError(PollError):
    """Poll option does not exist."""

    pass


class PollAnonymousError(PollError):
    """Cannot perform operation on anonymous poll."""

    pass


class PollEndedError(PollError):
    """Poll has already ended."""

    pass


class PollNotEndedError(PollError):
    """Poll has not ended yet."""

    pass


class InvalidPollQuestionError(PollError):
    """Poll question is invalid."""

    pass


class InvalidPollOptionError(PollError):
    """Poll option is invalid."""

    pass


class PollOptionLimitError(PollError):
    """Poll option count is invalid."""

    def __init__(self, message: str, min_options: int, max_options: int, actual: int):
        super().__init__(message)
        self.min_options = min_options
        self.max_options = max_options
        self.actual = actual


class InvalidPollDurationError(PollError):
    """Poll duration is invalid."""

    def __init__(self, message: str, min_hours: int, max_hours: int):
        super().__init__(message)
        self.min_hours = min_hours
        self.max_hours = max_hours


class AlreadyVotedError(PollError):
    """User has already voted on this poll."""

    pass


class MultipleVoteNotAllowedError(PollError):
    """Poll does not allow multiple choice voting."""

    pass


class PermissionDeniedError(PollError):
    """User does not have permission to perform this action."""

    pass


class MessageNotFoundError(PollError):
    """Message does not exist or is not accessible."""

    pass
