"""Shared exception types for the PlexiStash manager."""


class StashNotFoundError(LookupError):
    """Raised when an item is absent or not visible to the caller."""


class StashPermissionError(PermissionError):
    """Raised when a caller lacks the requested Stash permission."""


class StashValidationError(ValueError):
    """Raised for malformed Stash metadata."""


class StashStorageError(RuntimeError):
    """Raised when a referenced media object cannot be resolved."""
