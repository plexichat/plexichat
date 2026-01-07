"""
Sticker exceptions - All sticker-related error types.
"""

from typing import List


class StickerError(Exception):
    """Base exception for all sticker errors."""

    pass


class PackNotFoundError(StickerError):
    """Sticker pack does not exist."""

    pass


class StickerNotFoundError(StickerError):
    """Sticker does not exist."""

    pass


class PackLimitError(StickerError):
    """Maximum sticker packs limit reached."""

    def __init__(self, message: str, max_allowed: int, current: int):
        super().__init__(message)
        self.max_allowed = max_allowed
        self.current = current


class StickerLimitError(StickerError):
    """Maximum stickers per pack limit reached."""

    def __init__(self, message: str, max_allowed: int, current: int):
        super().__init__(message)
        self.max_allowed = max_allowed
        self.current = current


class InvalidStickerFormatError(StickerError):
    """Sticker format is not supported."""

    def __init__(self, message: str, format: str, allowed: List[str]):
        super().__init__(message)
        self.format = format
        self.allowed = allowed


class StickerTooLargeError(StickerError):
    """Sticker file size exceeds limit."""

    def __init__(self, message: str, max_size: int, actual_size: int):
        super().__init__(message)
        self.max_size = max_size
        self.actual_size = actual_size


class InvalidStickerNameError(StickerError):
    """Sticker name is invalid."""

    pass


class InvalidPackNameError(StickerError):
    """Pack name is invalid."""

    pass


class PermissionDeniedError(StickerError):
    """User does not have permission to perform this action."""

    def __init__(self, message: str, permission: str | None = None):
        super().__init__(message)
        self.permission = permission


class ServerNotFoundError(StickerError):
    """Server does not exist."""

    pass


class MessageNotFoundError(StickerError):
    """Message does not exist or is not accessible."""

    pass
