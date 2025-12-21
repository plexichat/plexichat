"""
User Features exceptions.
"""


class FeatureError(Exception):
    """Base exception for feature errors."""
    pass


class InvalidTierError(FeatureError):
    """Raised when an invalid tier is specified."""
    pass


class InvalidBadgeError(FeatureError):
    """Raised when an invalid badge is specified."""
    pass


class FeatureNotFoundError(FeatureError):
    """Raised when user features are not found."""
    pass


class PermissionDeniedError(FeatureError):
    """Raised when user doesn't have permission for an action."""
    pass


class UsageLimitExceededError(FeatureError):
    """Raised when a usage limit is exceeded."""
    def __init__(self, message: str, limit_type: str, current: int, max_allowed: int):
        super().__init__(message)
        self.limit_type = limit_type
        self.current = current
        self.max_allowed = max_allowed
