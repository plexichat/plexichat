"""
Auto-moderation exceptions.

All automod errors inherit from AutoModError for easy catching.
"""

from typing import Optional


class AutoModError(Exception):
    """Base exception for all automod errors."""
    pass


class RuleNotFoundError(AutoModError):
    """Raised when a rule is not found."""
    pass


class RuleValidationError(AutoModError):
    """Raised when rule configuration is invalid."""

    def __init__(self, message: str, issues: Optional[list] = None):
        super().__init__(message)
        self.issues = issues or []


class RuleDisabledError(AutoModError):
    """Raised when attempting to use a disabled rule."""
    pass


class ActionExecutionError(AutoModError):
    """Raised when an action fails to execute."""

    def __init__(self, message: str, action_type: Optional[str] = None):
        super().__init__(message)
        self.action_type = action_type


class ExemptionError(AutoModError):
    """Raised for exemption-related errors."""
    pass


class ViolationNotFoundError(AutoModError):
    """Raised when a violation record is not found."""
    pass


class ReputationError(AutoModError):
    """Raised for reputation system errors."""
    pass


class AIBackendError(AutoModError):
    """Raised when AI moderation backend fails."""

    def __init__(self, message: str, backend: Optional[str] = None, status_code: Optional[int] = None):
        super().__init__(message)
        self.backend = backend
        self.status_code = status_code


class AIBackendUnavailableError(AIBackendError):
    """Raised when AI backend is not configured or unavailable."""
    pass


class AIBackendTimeoutError(AIBackendError):
    """Raised when AI backend request times out."""
    pass


class RateLimitExceededError(AutoModError):
    """Raised when rate limit is exceeded for automod operations."""
    pass


class ConfigurationError(AutoModError):
    """Raised when automod configuration is invalid."""
    pass


class ServerNotFoundError(AutoModError):
    """Raised when server is not found."""
    pass


class ChannelNotFoundError(AutoModError):
    """Raised when channel is not found."""
    pass


class UserNotFoundError(AutoModError):
    """Raised when user is not found."""
    pass


class PermissionDeniedError(AutoModError):
    """Raised when user lacks permission for automod operation."""

    def __init__(self, message: str, permission: Optional[str] = None):
        super().__init__(message)
        self.permission = permission
