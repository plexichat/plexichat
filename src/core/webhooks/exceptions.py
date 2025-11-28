"""
Webhook exceptions - All webhook-related error types.
"""


class WebhookError(Exception):
    """Base exception for all webhook errors."""
    pass


class WebhookNotFoundError(WebhookError):
    """Webhook does not exist."""
    pass


class WebhookAccessDeniedError(WebhookError):
    """User does not have access to this webhook."""
    pass


class InvalidWebhookTokenError(WebhookError):
    """Webhook token is invalid or expired."""
    pass


class WebhookNameError(WebhookError):
    """Webhook name is invalid."""
    
    def __init__(self, message: str, max_length: int = 80):
        super().__init__(message)
        self.max_length = max_length


class WebhookAvatarError(WebhookError):
    """Webhook avatar URL is invalid."""
    pass


class WebhookLimitError(WebhookError):
    """Maximum webhooks limit reached."""
    
    def __init__(self, message: str, max_allowed: int, current: int):
        super().__init__(message)
        self.max_allowed = max_allowed
        self.current = current


class ChannelNotFoundError(WebhookError):
    """Channel does not exist or is not accessible."""
    pass


class PermissionDeniedError(WebhookError):
    """User does not have permission to perform this action."""
    
    def __init__(self, message: str, permission: str = None):
        super().__init__(message)
        self.permission = permission


class InvalidContentError(WebhookError):
    """Message content is invalid."""
    
    def __init__(self, message: str, issues: list = None):
        super().__init__(message)
        self.issues = issues or []


class EmbedLimitError(WebhookError):
    """Too many embeds in message."""
    
    def __init__(self, message: str, max_allowed: int, current: int):
        super().__init__(message)
        self.max_allowed = max_allowed
        self.current = current
