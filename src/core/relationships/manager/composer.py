"""
Composer for the RelationshipManager.
Assembles all mixins into the final RelationshipManager class via MRO.
"""

from typing import Any

from .friend_requests import FriendRequestsMixin
from .friends import FriendsMixin
from .blocking import BlockingMixin
from .status import RelationshipStatusMixin
from .mutual import MutualInfoMixin


class RelationshipManager(
    MutualInfoMixin,
    RelationshipStatusMixin,
    BlockingMixin,
    FriendsMixin,
    FriendRequestsMixin,
):
    """Complete RelationshipManager assembled from mixins via MRO."""

    def __init__(
        self, db: Any, auth_module: Any = None, servers_module: Any = None
    ) -> None:
        """Initialize the relationship manager.

        Args:
            db: Database instance (must be connected)
            auth_module: Optional auth module for user verification
            servers_module: Optional servers module for mutual servers
        """
        # super().__init__ is called via MRO through the mixins
        super().__init__(db, auth_module, servers_module)
