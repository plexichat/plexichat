"""Push module exceptions."""

from typing import Optional


class PermissionDeniedError(Exception):
    """Raised when a user is not allowed to perform a push operation."""

    def __init__(self, message: str, code: Optional[str] = None):
        super().__init__(message)
        self.message = message
        self.code = code
