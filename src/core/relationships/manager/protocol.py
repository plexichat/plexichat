"""
Protocol mixin for the RelationshipManager sub-package.

Defines the protocol interface that implementing classes must satisfy
to be used as part of the RelationshipManager composed via MRO.
"""

from typing import (
    Any,
    Callable,
    Dict,
    List,
    Optional,
    Protocol,
    TypeVar,
)

from src.core.base import SnowflakeID

from ..models import (
    BlockedUser,
    Friend,
    FriendRequest,
    MutualInfo,
    Relationship,
    RelationshipStatus,
)

_TransactionResult = TypeVar("_TransactionResult")


class RelationshipMixinProtocol(Protocol):
    """Protocol defining methods used across RelationshipManager mixins."""

    # Database
    _db: Any

    # Auth
    _auth: Any

    # Servers
    _servers: Any

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """
        Forward init through MRO chain.
        Protocol metaclass generates a synthetic __init__ that doesn't
        call super().__init__(), which would break the MRO chain when
        this Protocol is used as a mixin base class.
        """
        super().__init__(*args, **kwargs)

    # === Shared helpers ===

    def _validate_users(self, user_id: SnowflakeID, target_id: SnowflakeID) -> None: ...

    def _user_exists(self, user_id: SnowflakeID) -> bool: ...

    def _is_blocked(self, blocker_id: SnowflakeID, blocked_id: SnowflakeID) -> bool: ...

    def _is_blocked_by(self, user_id: SnowflakeID, other_id: SnowflakeID) -> bool: ...

    def _are_friends(self, user_id: SnowflakeID, other_id: SnowflakeID) -> bool: ...

    def _get_pending_request(
        self, sender_id: SnowflakeID, recipient_id: SnowflakeID
    ) -> Optional[Dict]: ...

    def _run_in_transaction(
        self, operation: Callable[[], _TransactionResult]
    ) -> _TransactionResult: ...

    def _invalidate_all_relationships_cache(self, *user_ids: SnowflakeID) -> None: ...

    def _get_timestamp(self) -> int: ...

    def _generate_id(self) -> SnowflakeID: ...

    # === Row converters ===

    def _row_to_friend_request(self, row: Any) -> FriendRequest: ...

    def _row_to_friend(self, row: Any) -> Friend: ...

    def _row_to_blocked_user(self, row: Any) -> BlockedUser: ...

    # === Friend request operations ===

    def send_friend_request(
        self,
        sender_id: SnowflakeID,
        recipient_id: SnowflakeID,
        message: Optional[str] = None,
    ) -> FriendRequest: ...

    def accept_friend_request(
        self, user_id: SnowflakeID, request_id: SnowflakeID
    ) -> FriendRequest: ...

    def decline_friend_request(
        self, user_id: SnowflakeID, request_id: SnowflakeID
    ) -> FriendRequest: ...

    def cancel_friend_request(
        self, user_id: SnowflakeID, request_id: SnowflakeID
    ) -> FriendRequest: ...

    def get_friend_request(
        self, request_id: SnowflakeID
    ) -> Optional[FriendRequest]: ...

    def get_pending_requests_incoming(
        self, user_id: SnowflakeID, limit: int = 100
    ) -> List[FriendRequest]: ...

    def get_pending_requests_outgoing(
        self, user_id: SnowflakeID, limit: int = 100
    ) -> List[FriendRequest]: ...

    def get_incoming_requests(
        self, user_id: SnowflakeID, limit: int = 100
    ) -> List[FriendRequest]: ...

    def get_outgoing_requests(
        self, user_id: SnowflakeID, limit: int = 100
    ) -> List[FriendRequest]: ...

    # === Friends operations ===

    def get_friends(self, user_id: SnowflakeID, limit: int = 100) -> List[Friend]: ...

    def get_friend_ids(self, user_id: SnowflakeID) -> List[SnowflakeID]: ...

    def remove_friend(self, user_id: SnowflakeID, friend_id: SnowflakeID) -> bool: ...

    # === Block operations ===

    def block_user(
        self,
        blocker_id: SnowflakeID,
        blocked_id: SnowflakeID,
        reason: Optional[str] = None,
    ) -> BlockedUser: ...

    def unblock_user(
        self, blocker_id: SnowflakeID, blocked_id: SnowflakeID
    ) -> bool: ...

    def get_block(self, block_id: SnowflakeID) -> Optional[BlockedUser]: ...

    def get_blocked_users(
        self, user_id: SnowflakeID, limit: int = 100
    ) -> List[BlockedUser]: ...

    def get_blocked_user_ids(self, user_id: SnowflakeID) -> List[SnowflakeID]: ...

    def get_all_blocked_ids(self, user_id: SnowflakeID) -> List[SnowflakeID]: ...

    def is_blocked(self, blocker_id: SnowflakeID, blocked_id: SnowflakeID) -> bool: ...

    def is_blocked_by_either(
        self, user_id: SnowflakeID, other_id: SnowflakeID
    ) -> bool: ...

    # === Relationship status ===

    def get_relationship(
        self, user_id: SnowflakeID, target_id: SnowflakeID
    ) -> Relationship: ...

    def get_all_relationships(self, user_id: SnowflakeID) -> Dict[str, List]: ...

    def get_relationship_status(
        self, user_id: SnowflakeID, target_id: SnowflakeID
    ) -> RelationshipStatus: ...

    # === Mutual information ===

    def get_mutual_friends(
        self, user_id: SnowflakeID, target_id: SnowflakeID
    ) -> List[SnowflakeID]: ...

    def get_mutual_friend_count(
        self, user_id: SnowflakeID, target_id: SnowflakeID
    ) -> int: ...

    def get_mutual_servers(
        self, user_id: SnowflakeID, target_id: SnowflakeID
    ) -> List[SnowflakeID]: ...

    def get_mutual_server_count(
        self, user_id: SnowflakeID, target_id: SnowflakeID
    ) -> int: ...

    def get_mutual_info(
        self, user_id: SnowflakeID, target_id: SnowflakeID
    ) -> MutualInfo: ...

    def get_suggested_friends(
        self, user_id: SnowflakeID, limit: int = 10
    ) -> List[SnowflakeID]: ...
