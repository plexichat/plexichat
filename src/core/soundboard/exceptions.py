"""
Soundboard exceptions - All soundboard-related error types.
"""

from typing import List


class SoundboardError(Exception):
    """Base exception for all soundboard errors."""

    pass


class SoundNotFoundError(SoundboardError):
    """Sound does not exist."""

    pass


class SoundLimitError(SoundboardError):
    """Maximum sounds limit reached."""

    def __init__(self, message: str, max_allowed: int, current: int):
        super().__init__(message)
        self.max_allowed = max_allowed
        self.current = current


class InvalidSoundFormatError(SoundboardError):
    """Sound format is not supported."""

    def __init__(self, message: str, format: str, allowed: List[str]):
        super().__init__(message)
        self.format = format
        self.allowed = allowed


class SoundTooLargeError(SoundboardError):
    """Sound file size exceeds limit."""

    def __init__(self, message: str, max_size: int, actual_size: int):
        super().__init__(message)
        self.max_size = max_size
        self.actual_size = actual_size


class SoundTooLongError(SoundboardError):
    """Sound duration exceeds limit."""

    def __init__(self, message: str, max_duration: float, actual_duration: float):
        super().__init__(message)
        self.max_duration = max_duration
        self.actual_duration = actual_duration


class InvalidSoundNameError(SoundboardError):
    """Sound name is invalid."""

    pass


class SoundCooldownError(SoundboardError):
    """Sound is on cooldown."""

    def __init__(self, message: str, remaining_seconds: int):
        super().__init__(message)
        self.remaining_seconds = remaining_seconds


class PermissionDeniedError(SoundboardError):
    """User does not have permission to perform this action."""

    def __init__(self, message: str, permission: str | None = None):
        super().__init__(message)
        self.permission = permission


class ServerNotFoundError(SoundboardError):
    """Server does not exist."""

    pass


class ChannelNotFoundError(SoundboardError):
    """Channel does not exist or is not a voice channel."""

    pass


class NotInVoiceChannelError(SoundboardError):
    """User is not in a voice channel."""

    pass
