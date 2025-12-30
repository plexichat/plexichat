"""
Server exceptions - All server-related error types.
"""


class ServerError(Exception):
    """Base exception for all server errors."""
    pass


class ServerNotFoundError(ServerError):
    """Server does not exist or user has no access."""
    pass


class ServerAccessDeniedError(ServerError):
    """User does not have permission for this server operation."""
    pass


class ChannelNotFoundError(ServerError):
    """Channel does not exist or user has no access."""
    pass


class ChannelAccessDeniedError(ServerError):
    """User does not have permission for this channel operation."""
    pass


class ChannelTypeError(ServerError):
    """Operation not supported for this channel type."""
    pass


class CategoryNotFoundError(ServerError):
    """Category does not exist."""
    pass


class RoleNotFoundError(ServerError):
    """Role does not exist."""
    pass


class RoleAccessDeniedError(ServerError):
    """User does not have permission for this role operation."""
    pass


class RoleHierarchyError(ServerError):
    """Cannot modify role due to hierarchy constraints."""

    def __init__(self, message: str, user_position: int = 0, target_position: int = 0):
        super().__init__(message)
        self.user_position = user_position
        self.target_position = target_position


class DefaultRoleError(ServerError):
    """Cannot delete or modify the default role in certain ways."""
    pass


class MemberNotFoundError(ServerError):
    """Member does not exist in this server."""
    pass


class MemberExistsError(ServerError):
    """User is already a member of this server."""
    pass


class InviteNotFoundError(ServerError):
    """Invite does not exist or has been revoked."""
    pass


class InviteExpiredError(ServerError):
    """Invite has expired."""

    def __init__(self, message: str, expired_at: int = 0):
        super().__init__(message)
        self.expired_at = expired_at


class InviteMaxUsesError(ServerError):
    """Invite has reached maximum uses."""

    def __init__(self, message: str, max_uses: int = 0, current_uses: int = 0):
        super().__init__(message)
        self.max_uses = max_uses
        self.current_uses = current_uses


class BanExistsError(ServerError):
    """User is already banned from this server."""
    pass


class BanNotFoundError(ServerError):
    """User is not banned from this server."""
    pass


class UserBannedError(ServerError):
    """User is banned from this server."""
    pass


class InvalidServerNameError(ServerError):
    """Server name is invalid."""

    def __init__(self, message: str, name: str = ""):
        super().__init__(message)
        self.name = name


class InvalidChannelNameError(ServerError):
    """Channel name is invalid."""

    def __init__(self, message: str, name: str = ""):
        super().__init__(message)
        self.name = name


class InvalidRoleNameError(ServerError):
    """Role name is invalid."""

    def __init__(self, message: str, name: str = ""):
        super().__init__(message)
        self.name = name


class PermissionDeniedError(ServerError):
    """User does not have the required permission."""

    def __init__(self, message: str, permission: str = ""):
        super().__init__(message)
        self.permission = permission


class OwnerCannotLeaveError(ServerError):
    """Server owner cannot leave without transferring ownership."""
    pass


class CannotModifyOwnerError(ServerError):
    """Cannot kick, ban, or modify the server owner."""
    pass


class ScheduledEventNotFoundError(ServerError):
    """Scheduled event does not exist."""
    pass


class ScheduledEventError(ServerError):
    """Error with scheduled event operation."""
    pass


class InvalidEventTimeError(ServerError):
    """Event time is invalid."""

    def __init__(self, message: str, start_time: int = 0, end_time: int = 0):
        super().__init__(message)
        self.start_time = start_time
        self.end_time = end_time


class TemplateNotFoundError(ServerError):
    """Template does not exist."""
    pass


class TemplateError(ServerError):
    """Error with template operation."""
    pass


class InvalidTemplateCodeError(ServerError):
    """Template code is invalid."""

    def __init__(self, message: str, code: str = ""):
        super().__init__(message)
        self.code = code


class WelcomeScreenNotFoundError(ServerError):
    """Welcome screen does not exist."""
    pass


class OnboardingStepNotFoundError(ServerError):
    """Onboarding step does not exist."""
    pass


class OnboardingError(ServerError):
    """Error with onboarding operation."""
    pass
