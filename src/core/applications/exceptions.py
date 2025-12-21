"""
Application exceptions - All application-related error types.
"""

from typing import Optional


class ApplicationError(Exception):
    """Base exception for all application errors."""
    pass


class ApplicationNotFoundError(ApplicationError):
    """Application does not exist."""
    pass


class ApplicationAccessDeniedError(ApplicationError):
    """User does not have access to this application."""
    pass


class ApplicationLimitError(ApplicationError):
    """Maximum applications limit reached."""

    def __init__(self, message: str, max_allowed: int, current: int):
        super().__init__(message)
        self.max_allowed = max_allowed
        self.current = current


class InvalidApplicationNameError(ApplicationError):
    """Application name is invalid (too short, too long, or reserved)."""
    pass


class CommandNotFoundError(ApplicationError):
    """Command does not exist."""
    pass


class CommandLimitError(ApplicationError):
    """Maximum commands limit reached."""

    def __init__(self, message: str, max_allowed: int, current: int):
        super().__init__(message)
        self.max_allowed = max_allowed
        self.current = current


class CommandValidationError(ApplicationError):
    """Command data failed validation."""

    def __init__(self, message: str, issues: Optional[list] = None):
        super().__init__(message)
        self.issues = issues or []


class CommandOptionLimitError(ApplicationError):
    """Maximum command options limit reached."""

    def __init__(self, message: str, max_allowed: int, current: int):
        super().__init__(message)
        self.max_allowed = max_allowed
        self.current = current


class InteractionNotFoundError(ApplicationError):
    """Interaction does not exist."""
    pass


class InteractionExpiredError(ApplicationError):
    """Interaction token has expired."""
    pass


class InteractionAlreadyRespondedError(ApplicationError):
    """Interaction has already been responded to."""
    pass


class InteractionValidationError(ApplicationError):
    """Interaction data failed validation."""

    def __init__(self, message: str, issues: Optional[list] = None):
        super().__init__(message)
        self.issues = issues or []


class ComponentValidationError(ApplicationError):
    """Component data failed validation."""

    def __init__(self, message: str, issues: Optional[list] = None):
        super().__init__(message)
        self.issues = issues or []


class OAuth2Error(ApplicationError):
    """Base exception for OAuth2 errors."""

    def __init__(self, error: str, error_description: Optional[str] = None):
        super().__init__(error_description or error)
        self.error = error
        self.error_description = error_description


class InvalidClientError(OAuth2Error):
    """Invalid client credentials."""

    def __init__(self, description: Optional[str] = None):
        super().__init__("invalid_client", description or "Invalid client credentials")


class InvalidGrantError(OAuth2Error):
    """Invalid authorization grant."""

    def __init__(self, description: Optional[str] = None):
        super().__init__("invalid_grant", description or "Invalid authorization grant")


class InvalidScopeError(OAuth2Error):
    """Invalid or unknown scope."""

    def __init__(self, description: Optional[str] = None):
        super().__init__("invalid_scope", description or "Invalid or unknown scope")


class InvalidRedirectUriError(OAuth2Error):
    """Invalid redirect URI."""

    def __init__(self, description: Optional[str] = None):
        super().__init__("invalid_request", description or "Invalid redirect URI")


class AuthorizationCodeExpiredError(OAuth2Error):
    """Authorization code has expired."""

    def __init__(self):
        super().__init__("invalid_grant", "Authorization code has expired")


class TokenExpiredError(OAuth2Error):
    """Access token has expired."""

    def __init__(self):
        super().__init__("invalid_token", "Access token has expired")


class TokenRevokedError(OAuth2Error):
    """Token has been revoked."""

    def __init__(self):
        super().__init__("invalid_token", "Token has been revoked")


class InstallationNotFoundError(ApplicationError):
    """Application installation does not exist."""
    pass


class InstallationExistsError(ApplicationError):
    """Application is already installed on this server."""
    pass


class WebhookSignatureError(ApplicationError):
    """Webhook signature verification failed."""
    pass


class WebhookDeliveryError(ApplicationError):
    """Webhook delivery failed."""

    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


class RateLimitError(ApplicationError):
    """Rate limit exceeded for application."""

    def __init__(self, message: str, retry_after: float):
        super().__init__(message)
        self.retry_after = retry_after


class PermissionDeniedError(ApplicationError):
    """User does not have permission to perform this action."""

    def __init__(self, message: str, permission: Optional[str] = None):
        super().__init__(message)
        self.permission = permission
