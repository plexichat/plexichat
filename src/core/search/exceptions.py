"""
Search exceptions - All search-related error types.
"""


class SearchError(Exception):
    """Base exception for all search errors."""
    pass


class SearchNotFoundError(SearchError):
    """Search result or indexed item not found."""
    pass


class SearchPermissionError(SearchError):
    """User does not have permission to search or access results."""

    def __init__(self, message: str, permission: str | None = None):
        super().__init__(message)
        self.permission = permission


class SearchQueryError(SearchError):
    """Error in search query."""
    pass


class InvalidQuerySyntaxError(SearchQueryError):
    """Invalid query syntax."""

    def __init__(self, message: str, position: int | None = None, suggestion: str | None = None):
        super().__init__(message)
        self.position = position
        self.suggestion = suggestion


class SearchIndexError(SearchError):
    """Error during indexing operation."""

    def __init__(self, message: str, item_id: int | None = None):
        super().__init__(message)
        self.item_id = item_id


class SearchBackendError(SearchError):
    """Error communicating with search backend."""

    def __init__(self, message: str, backend: str | None = None, original_error: Exception | None = None):
        super().__init__(message)
        self.backend = backend
        self.original_error = original_error


class SearchLimitError(SearchError):
    """Search limit exceeded."""

    def __init__(self, message: str, max_allowed: int, requested: int):
        super().__init__(message)
        self.max_allowed = max_allowed
        self.requested = requested


class DiscoveryError(SearchError):
    """Error in server discovery operations."""
    pass


class ServerNotListedError(DiscoveryError):
    """Server is not listed in the discovery directory."""

    def __init__(self, message: str, server_id: int | None = None):
        super().__init__(message)
        self.server_id = server_id


class VerificationError(DiscoveryError):
    """Error in server verification."""

    def __init__(self, message: str, server_id: int | None = None, reason: str | None = None):
        super().__init__(message)
        self.server_id = server_id
        self.reason = reason


class BumpCooldownError(DiscoveryError):
    """Server bump is on cooldown."""

    def __init__(self, message: str, server_id: int, cooldown_remaining: int):
        super().__init__(message)
        self.server_id = server_id
        self.cooldown_remaining = cooldown_remaining


class CategoryNotFoundError(DiscoveryError):
    """Server category does not exist."""

    def __init__(self, message: str, category: str | None = None):
        super().__init__(message)
        self.category = category


class MinimumMembersError(DiscoveryError):
    """Server does not meet minimum member requirement for listing."""

    def __init__(self, message: str, required: int, current: int):
        super().__init__(message)
        self.required = required
        self.current = current
