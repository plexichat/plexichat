"""
Embed exceptions - All embed-related error types.
"""

from typing import Optional


class EmbedError(Exception):
    """Base exception for all embed errors."""
    pass


class EmbedNotFoundError(EmbedError):
    """Embed does not exist."""
    pass


class EmbedValidationError(EmbedError):
    """Embed data failed validation."""
    
    def __init__(self, message: str, issues: Optional[list] = None):
        super().__init__(message)
        self.issues = issues or []


class EmbedLimitError(EmbedError):
    """Maximum embeds limit reached on message."""
    
    def __init__(self, message: str, max_allowed: int, current: int):
        super().__init__(message)
        self.max_allowed = max_allowed
        self.current = current


class EmbedFieldLimitError(EmbedError):
    """Maximum fields limit reached on embed."""
    
    def __init__(self, message: str, max_allowed: int, current: int):
        super().__init__(message)
        self.max_allowed = max_allowed
        self.current = current


class EmbedCharacterLimitError(EmbedError):
    """Total character limit exceeded."""
    
    def __init__(self, message: str, max_allowed: int, current: int):
        super().__init__(message)
        self.max_allowed = max_allowed
        self.current = current


class InvalidUrlError(EmbedError):
    """URL is invalid or not allowed."""
    
    def __init__(self, message: str, url: Optional[str] = None):
        super().__init__(message)
        self.url = url


class InvalidColorError(EmbedError):
    """Color format is invalid."""
    
    def __init__(self, message: str, color: Optional[str] = None):
        super().__init__(message)
        self.color = color


class MessageNotFoundError(EmbedError):
    """Message does not exist or is not accessible."""
    pass


class PermissionDeniedError(EmbedError):
    """User does not have permission to perform this action."""
    
    def __init__(self, message: str, permission: Optional[str] = None):
        super().__init__(message)
        self.permission = permission


class EmbedSanitizationError(EmbedError):
    """Content failed sanitization (potential XSS or unsafe content)."""
    
    def __init__(self, message: str, field: Optional[str] = None):
        super().__init__(message)
        self.field = field
