from typing import Optional, List

from src.core.base import SnowflakeID

from ..models import AuditLogEntry, AuditLogAction
from ..exceptions import ServerNotFoundError
from .converters import _row_to_audit_entry
from .protocol import ServerProtocol


class AuditOpsMixin(ServerProtocol):
    """Mixin for audit log operations."""

    def get_audit_log(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
        limit: int = 50,
        action_type: Optional[AuditLogAction] = None,
        before_id: Optional[SnowflakeID] = None,
    ) -> List[AuditLogEntry]:
        """Get audit log entries for a server."""
        server = self.get_server(server_id, user_id)
        if not server:
            raise ServerNotFoundError("Server not found")

        self.require_permission(user_id, server_id, "server.view_audit_log")

        limit = min(limit, 100)

        query = "SELECT * FROM srv_audit_log WHERE server_id = ?"
        params: List[SnowflakeID | str | int] = [server_id]

        if action_type:
            query += " AND action = ?"
            params.append(action_type.value)

        if before_id:
            query += " AND id < ?"
            params.append(before_id)

        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        rows = self._db.fetch_all(query, tuple(params))

        return [_row_to_audit_entry(row) for row in rows]
