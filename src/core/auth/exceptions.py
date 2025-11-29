"""
Authentication exceptions.

All auth-related errors inherit from AuthError for easy catching.
"""

from typing import Optional


class AuthError(Exception):
    """Base exception for all authentication errors."""
    pass


class InvalidCredentialsError(AuthError):
    """Raised when username or password is incorrect."""
    pass


class AccountLockedError(AuthError):
    """Raised when account is temporarily locked due to failed attempts."""
    
    def __init__(self, message: str, locked_until: Optional[int] = None):
        super().__init__(message)
        self.locked_until = locked_until


class AccountDisabledError(AuthError):
    """Raised when account is permanently disabled."""
    pass


class EmailNotVerifiedError(AuthError):
    """Raised when email verification is required but not completed."""
    pass


class TokenExpiredError(AuthError):
    """Raised when a token has expired."""
    pass


class TokenInvalidError(AuthError):
    """Raised when a token is malformed or invalid."""
    pass


class TwoFactorRequiredError(AuthError):
    """Raised when 2FA is required to complete authentication."""
    
    def __init__(self, message: str, challenge_token: str, methods: list):
        super().__init__(message)
        self.challenge_token = challenge_token
        self.methods = methods


class TwoFactorInvalidError(AuthError):
    """Raised when 2FA code is invalid."""
    pass


class PermissionDeniedError(AuthError):
    """Raised when user lacks required permission."""
    pass


class UserExistsError(AuthError):
    """Raised when trying to create a user that already exists."""
    
    def __init__(self, message: str, field: Optional[str] = None):
        super().__init__(message)
        self.field = field  # 'username' or 'email'


class UserNotFoundError(AuthError):
    """Raised when a user cannot be found."""
    pass


class WeakPasswordError(AuthError):
    """Raised when password does not meet strength requirements."""
    
    def __init__(self, message: str, issues: list):
        super().__init__(message)
        self.issues = issues


class BotLimitExceededError(AuthError):
    """Raised when user has reached maximum bot limit."""
    pass


class InvalidUsernameError(AuthError):
    """Raised when username format is invalid."""
    
    def __init__(self, message: str, issues: list):
        super().__init__(message)
        self.issues = issues


class InvalidEmailError(AuthError):
    """Raised when email format is invalid."""
    pass


class TwoFactorSetupError(AuthError):
    """Raised when there is an error during 2FA setup."""
    pass


class SessionLimitExceededError(AuthError):
    """Raised when user has too many active sessions."""
    pass
