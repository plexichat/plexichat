"""
Voice exceptions - All voice-related error types.
"""


class VoiceError(Exception):
    """Base exception for all voice errors."""
    pass


class ChannelNotFoundError(VoiceError):
    """Voice channel does not exist."""
    pass


class ChannelAccessDeniedError(VoiceError):
    """User does not have permission to access this voice channel."""

    def __init__(self, message: str, permission: str = ""):
        super().__init__(message)
        self.permission = permission


class ChannelFullError(VoiceError):
    """Voice channel has reached its user limit."""

    def __init__(self, message: str, limit: int = 0, current: int = 0):
        super().__init__(message)
        self.limit = limit
        self.current = current


class ChannelTypeError(VoiceError):
    """Operation not supported for this channel type."""

    def __init__(self, message: str, expected: str = "", actual: str = ""):
        super().__init__(message)
        self.expected = expected
        self.actual = actual


class UserNotInChannelError(VoiceError):
    """User is not in a voice channel."""
    pass


class UserAlreadyInChannelError(VoiceError):
    """User is already in a voice channel."""

    def __init__(self, message: str, channel_id: int = 0):
        super().__init__(message)
        self.channel_id = channel_id


class StageNotFoundError(VoiceError):
    """Stage instance does not exist."""
    pass


class SpeakerRequestNotFoundError(VoiceError):
    """Speaker request does not exist."""
    pass


class SpeakerRequestExistsError(VoiceError):
    """User already has a pending speaker request."""
    pass


class NotSpeakerError(VoiceError):
    """User is not a speaker in the stage channel."""
    pass


class AlreadySpeakerError(VoiceError):
    """User is already a speaker in the stage channel."""
    pass


class PermissionDeniedError(VoiceError):
    """User does not have the required permission."""

    def __init__(self, message: str, permission: str = ""):
        super().__init__(message)
        self.permission = permission


class InvalidVoiceStateError(VoiceError):
    """Invalid voice state operation."""
    pass


class UserNotFoundError(VoiceError):
    """User does not exist."""
    pass
