"""Rate limiting exceptions."""


class RateLimitError(Exception):
    """Raised when a rate limit is exceeded."""

    def __init__(self, message: str, retry_after: float):
        self.message = message
        self.retry_after = retry_after
        super().__init__(message)
