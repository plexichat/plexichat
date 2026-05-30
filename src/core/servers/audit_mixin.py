"""Audit log operations mixin."""

from typing import Any, List, Optional

from src.core.base import SnowflakeID

from .models import AuditLogEntry, AuditLogAction


class AuditMixin:
    """Mixin for audit log operations.

    Provides: get_audit_log
    """

    _manager: Any = None

    def get_audit_log(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
        limit: int = 50,
        action_type: Optional[AuditLogAction] = None,
        before_id: Optional[SnowflakeID] = None,
    ) -> List[AuditLogEntry]:
        """Get audit log entries for a server."""
        return self._manager.get_audit_log(
            user_id, server_id, limit, action_type, before_id
        )
