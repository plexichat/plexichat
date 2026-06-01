"""
Audit log handler for server operations.
"""

import json
from typing import Optional, List, Dict, Any
from src.core.base import SnowflakeID
from src.utils.encryption import encrypt_data
import utils.logger as logger
from ..models import AuditLogEntry, AuditLogAction
from ..manager.converters import _row_to_audit_entry


class AuditHandler:
    def __init__(self, manager):
        self.manager = manager
        self.db = manager._db

    def log_audit(
        self,
        server_id: SnowflakeID,
        user_id: SnowflakeID,
        action: AuditLogAction,
        target_type: Optional[str] = None,
        target_id: Optional[SnowflakeID] = None,
        changes: Optional[Dict[str, Any]] = None,
        reason: Optional[str] = None,
    ) -> None:
        """Log an audit entry."""
        entry_id = self.manager._generate_id()
        now = self.manager._get_timestamp()

        changes_json = json.dumps(changes) if changes else None
        changes_encrypted = None
        if changes_json:
            try:
                changes_encrypted = encrypt_data(
                    changes_json, context=f"audit:{entry_id}"
                )
            except Exception as e:
                logger.warning(
                    f"Failed to encrypt audit log changes for {entry_id}: {e}"
                )

        self.db.execute(
            """INSERT INTO srv_audit_log
               (id, server_id, user_id, action, target_type, target_id, changes, changes_encrypted, reason, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                entry_id,
                server_id,
                user_id,
                action.value,
                target_type,
                target_id,
                changes_json,
                changes_encrypted,
                reason,
                now,
            ),
        )

    def get_audit_log(
        self,
        server_id: SnowflakeID,
        limit: int = 50,
        action_type: Optional[AuditLogAction] = None,
        before_id: Optional[SnowflakeID] = None,
    ) -> List[AuditLogEntry]:
        """Get audit log entries for a server."""
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

        rows = self.db.fetch_all(query, tuple(params))

        return [_row_to_audit_entry(row) for row in rows]
