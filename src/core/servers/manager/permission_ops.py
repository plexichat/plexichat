from typing import Dict, Optional

from src.core.base import SnowflakeID

from ..exceptions import PermissionDeniedError
from .protocol import ServerProtocol


class PermissionOpsMixin(ServerProtocol):
    """Mixin for permission operations."""

    def has_permission(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
        permission: str,
        channel_id: Optional[SnowflakeID] = None,
    ) -> bool:
        return self.role_handler.has_permission(
            user_id, server_id, permission, channel_id
        )

    def get_permissions(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
        channel_id: Optional[SnowflakeID] = None,
    ) -> Dict[str, bool]:
        return self.role_handler.get_permissions(user_id, server_id, channel_id)

    def require_permission(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
        permission: str,
        channel_id: Optional[SnowflakeID] = None,
    ) -> None:
        if not self.has_permission(user_id, server_id, permission, channel_id):
            raise PermissionDeniedError(
                f"Missing required permission: {permission}", permission
            )
