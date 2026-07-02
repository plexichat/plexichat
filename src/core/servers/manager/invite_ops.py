from typing import List

from src.core.base import SnowflakeID

from ..exceptions import ServerNotFoundError
from .protocol import ServerProtocol
from .converters import _row_to_invite


class InviteOpsMixin(ServerProtocol):
    """Mixin for invite operations."""

    def get_invites(self, user_id: SnowflakeID, server_id: SnowflakeID) -> List:
        server = self.get_server(server_id, user_id)
        if not server:
            raise ServerNotFoundError("Server not found")

        self.require_permission(user_id, server_id, "invites.manage")

        rows = self._db.fetch_all(
            "SELECT * FROM srv_invites WHERE server_id = ? AND revoked = 0 ORDER BY created_at DESC",
            (server_id,),
        )

        return [_row_to_invite(row) for row in rows]

    def get_server_invites(self, user_id: SnowflakeID, server_id: SnowflakeID) -> List:
        return self.get_invites(user_id, server_id)
