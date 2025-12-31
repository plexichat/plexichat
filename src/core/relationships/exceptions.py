"""
Relationship exceptions - All relationship-related error types.
"""


class RelationshipError(Exception):
    """Base exception for all relationship errors."""
    pass


class UserNotFoundError(RelationshipError):
    """User does not exist."""
    pass


class SelfRelationshipError(RelationshipError):
    """Cannot create relationship with self."""
    pass


class FriendRequestNotFoundError(RelationshipError):
    """Friend request does not exist."""
    pass


class FriendRequestExistsError(RelationshipError):
    """Friend request already exists."""
    pass


class AlreadyFriendsError(RelationshipError):
    """Users are already friends."""
    pass


class NotFriendsError(RelationshipError):
    """Users are not friends."""
    pass


class UserBlockedError(RelationshipError):
    """Cannot perform action because user is blocked."""

    def __init__(self, message: str, blocked_by: int | None = None, blocked_user: int | None = None):
        super().__init__(message)
        self.blocked_by = blocked_by
        self.blocked_user = blocked_user


class AlreadyBlockedError(RelationshipError):
    """User is already blocked."""
    pass


class NotBlockedError(RelationshipError):
    """User is not blocked."""
    pass


class CannotBlockSelfError(RelationshipError):
    """Cannot block yourself."""
    pass


class PermissionDeniedError(RelationshipError):
    """User does not have permission to perform this action."""
    pass
