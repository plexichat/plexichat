"""
Organization exceptions.
"""


class OrganizationError(Exception):
    """Base exception for organization errors."""
    pass


class OrgNotFoundError(OrganizationError):
    """Raised when organization is not found."""
    pass


class OrgExistsError(OrganizationError):
    """Raised when organization already exists."""
    pass


class MemberNotFoundError(OrganizationError):
    """Raised when member is not found."""
    pass


class InviteNotFoundError(OrganizationError):
    """Raised when invite is not found."""
    pass


class InviteExpiredError(OrganizationError):
    """Raised when invite has expired."""
    pass


class PermissionDeniedError(OrganizationError):
    """Raised when user doesn't have permission."""
    pass


class FeatureRequiredError(OrganizationError):
    """Raised when a required feature flag is missing."""
    pass
