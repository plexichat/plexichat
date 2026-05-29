from typing import Optional, List, Any

from src.core.base import SnowflakeID
from ..models import ActionType, AuditEntry
from .converters import row_to_audit_entry


from .protocol import AutoModProtocol


class AuditMixin(AutoModProtocol):
    def get_audit_log(
        self,
        server_id: SnowflakeID,
        limit: int = 50,
        before_id: Optional[SnowflakeID] = None,
        action_type: Optional[ActionType] = None,
    ) -> List[AuditEntry]:
        query = "SELECT * FROM automod_audit WHERE server_id = ?"
        params: List[Any] = [server_id]

        if action_type is not None:
            query += " AND action_type = ?"
            params.append(action_type.value)

        if before_id is not None:
            query += " AND id < ?"
            params.append(before_id)

        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(min(limit, 100))

        rows = self._db.fetch_all(query, tuple(params))
        return [row_to_audit_entry(row) for row in rows]
