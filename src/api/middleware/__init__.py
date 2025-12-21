"""
API middleware - Authentication, error handling, logging, and rate limiting middleware.
"""

from .authentication import AuthenticationMiddleware, get_current_user, get_optional_user
from .error_handling import setup_exception_handlers, ErrorHandlingMiddleware
from .logging import LoggingMiddleware
from .rate_limiting import (
    get_user_info_from_request,
    create_rate_limit_middleware,
    RateLimitMiddleware,
)

__all__ = [
    "AuthenticationMiddleware",
    "get_current_user",
    "get_optional_user",
    "setup_exception_handlers",
    "ErrorHandlingMiddleware",
    "LoggingMiddleware",
    "RateLimitMiddleware",
    "get_user_info_from_request",
    "create_rate_limit_middleware",
]
