"""
API middleware - Authentication, error handling, and logging middleware.
"""

from .authentication import AuthenticationMiddleware, get_current_user, get_optional_user
from .error_handling import setup_exception_handlers
from .logging import LoggingMiddleware

__all__ = [
    "AuthenticationMiddleware",
    "get_current_user",
    "get_optional_user",
    "setup_exception_handlers",
    "LoggingMiddleware",
]
