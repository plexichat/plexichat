"""
Audit log handler for server operations.
"""

import json
import threading
from contextlib import contextmanager
from typing import Iterator, Optional, List, Dict, Any, Tuple
from src.core.base import SnowflakeID
from .audit_write_buffer import AuditWriteBuffer
from src.utils.encryption import encrypt_data
import utils.logger as logger
from ..models import AuditLogEntry, AuditLogAction
from ..manager.converters import _row_to_audit_entry


_INSERT_AUDIT_SQL = """INSERT INTO srv_audit_log
               (id, server_id, user_id, action, target_type, target_id, changes, changes_encrypted, reason, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""


class AuditHandler:
    def __init__(self, manager):
        self.manager = manager
        self.db = manager._db
        # The active buffer is thread-local: a handler instance is shared
        # across request threads, and a bulk operation on one thread must
        # never capture audit rows emitted by another.
        self._local = threading.local()

    def _build_row(
        self,
        server_id: SnowflakeID,
        user_id: SnowflakeID,
        action: AuditLogAction,
        target_type: Optional[str],
        target_id: Optional[SnowflakeID],
        changes: Optional[Dict[str, Any]],
        reason: Optional[str],
    ) -> Tuple[Any, ...]:
        """Build one audit row, capturing id/time/encryption at emit time."""
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

        return (
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
        )

    @contextmanager
    def buffered(self, capacity: int = 256) -> Iterator[AuditWriteBuffer]:
        """Batch ``log_audit`` writes emitted inside this block.

        Accepted rows are flushed with one ``execute_many`` per ``capacity``
        rows and once more on exit -- including exceptional exit -- so audit
        completeness is preserved.  Outside this block ``log_audit`` keeps its
        original immediate-write behaviour.
        """
        if getattr(self._local, "buffer", None) is not None:
            # Already buffering on this thread: reuse the outer buffer so a
            # nested bulk operation cannot lose rows or double-flush.
            yield self._local.buffer
            return

        buffer = AuditWriteBuffer(self.db, _INSERT_AUDIT_SQL, capacity=capacity)
        self._local.buffer = buffer
        try:
            yield buffer
        finally:
            self._local.buffer = None
            buffer.close()

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
        """Log an audit entry (immediately, or into the active bulk buffer)."""
        row = self._build_row(
            server_id, user_id, action, target_type, target_id, changes, reason
        )
        buffer = getattr(self._local, "buffer", None)
        if buffer is not None:
            buffer.add(row)
        else:
            self.db.execute(_INSERT_AUDIT_SQL, row)

    def get_audit_log(
        self,
        server_id: SnowflakeID,
        limit: int = 50,
        action_type: Optional[AuditLogAction] = None,
        before_id: Optional[SnowflakeID] = None,
    ) -> List[AuditLogEntry]:
        """Get audit log entries for a server."""
        # Clamp from below too: SQLite treats LIMIT -1 as *unlimited*, so a
        # negative limit would bypass the page cap and dump the whole log.
        limit = max(0, min(limit, 100))

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
