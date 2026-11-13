"""Attribute contract shared by the role handler mixins.

``RoleHandler`` supplies the manager and database handles, and the mixins call
each other (CRUD resolves permissions, assignments resolve roles). Declaring
that surface here keeps pyright happy without each module repeating the full
manager API.
"""

from typing import Any, Dict, Optional, Protocol

from src.core.base import SnowflakeID

from ...models import Role


class RoleHandlerProtocol(Protocol):
    """What every role mixin may rely on from its composing class."""

    manager: Any
    db: Any

    def get_role(self, role_id: SnowflakeID, user_id: SnowflakeID) -> Optional[Role]:
        """Return the role if the caller is a member of its server."""
        ...

    def get_permissions(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
        channel_id: Optional[SnowflakeID] = None,
    ) -> Dict[str, bool]:
        """Resolve the caller's effective permissions in a server/channel."""
        ...
