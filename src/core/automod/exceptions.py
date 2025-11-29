"""
AutoMod exceptions - All automod-related error types.
"""


class AutoModError(Exception):
    """Base exception for all automod errors."""
    pass


class RuleNotFoundError(AutoModError):
    """Rule does not exist."""
    pass


class RuleValidationError(AutoModError):
    """Rule configuration failed validation."""
    
    def __init__(self, message: str, issues: list = None):
        super().__init__(message)
        self.issues = issues or []


class RuleLimitError(AutoModError):
    """Maximum rules limit reached for server."""
    
    def __init__(self, message: str, max_allowed: int, current: int):
        super().__init__(message)
        self.max_allowed = max_allowed
        self.current = current


class ViolationNotFoundError(AutoModError):
    """Violation record does not exist."""
    pass


class ServerConfigNotFoundError(AutoModError):
    """Server automod configuration does not exist."""
    pass


class ExemptionError(AutoModError):
    """Error managing exemptions."""
    pass


class ExemptionExistsError(ExemptionError):
    """Exemption already exists."""
    pass


class ExemptionNotFoundError(ExemptionError):
    """Exemption does not exist."""
    pass


class ActionExecutionError(AutoModError):
    """Failed to execute an automod action."""
    
    def __init__(self, message: str, action_type: str = None, original_error: Exception = None):
        super().__init__(message)
        self.action_type = action_type
        self.original_error = original_error


class AIBackendError(AutoModError):
    """Error communicating with AI moderation backend."""
    
    def __init__(self, message: str, backend: str = None, status_code: int = None):
        super().__init__(message)
        self.backend = backend
        self.status_code = status_code


class AIConfigurationError(AutoModError):
    """AI backend is not properly configured."""
    
    def __init__(self, message: str, backend: str = None, missing_config: str = None):
        super().__init__(message)
        self.backend = backend
        self.missing_config = missing_config


class ReputationError(AutoModError):
    """Error managing user reputation."""
    pass


class PermissionDeniedError(AutoModError):
    """User does not have permission to perform this action."""
    
    def __init__(self, message: str, permission: str = None):
        super().__init__(message)
        self.permission = permission


class RateLimitError(AutoModError):
    """Rate limit exceeded for automod operations."""
    
    def __init__(self, message: str, retry_after_seconds: int = None):
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class InvalidPatternError(AutoModError):
    """Invalid regex pattern in rule configuration."""
    
    def __init__(self, message: str, pattern: str = None):
        super().__init__(message)
        self.pattern = pattern
