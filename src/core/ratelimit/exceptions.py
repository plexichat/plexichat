"""Rate limiting exceptions."""


class RateLimitError(Exception):
    """Raised when a rate limit is exceeded."""

    def __init__(self, message: str, retry_after: float):
        self.message = message
        self.retry_after = retry_after
        super().__init__(message)


class RateLimitStoreUnavailable(RuntimeError):
    """The rate-limit backend could not serve a decision.

    Callers must treat this as a denial, never as permission to let the
    request through. The manager translates it into a fail-closed
    :class:`~.models.RateLimitResult`; storage backends raise it when an
    operation cannot be completed safely (e.g. a distributed lock could not
    be acquired).
    """
