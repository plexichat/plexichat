"""
Attachment repository - Data access for message attachments.
"""

from typing import Any, Dict, List, Optional

from ..models import Attachment
from .base import BaseRepository
from src.core.base import SnowflakeID
from src.utils.encryption import decrypt_data


class AttachmentRepository(BaseRepository[Attachment]):
    """Repository for attachment data access."""

    def create(
        self,
        att_id: SnowflakeID,
        message_id: SnowflakeID,
        filename: str,
        content_type: str,
        size: int,
        url: str,
        created_at: int,
        url_encrypted: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        checksum: Optional[str] = None,
        auto_commit: bool = True,
    ) -> None:
        """Create a new attachment."""
        self._execute(
            """INSERT INTO msg_attachments 
               (id, message_id, filename, content_type, size, url, url_encrypted, created_at, metadata, checksum)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                att_id,
                message_id,
                filename,
                content_type,
                size,
                url,
                url_encrypted,
                created_at,
                self._json_dumps(metadata),
                checksum,
            ),
            auto_commit=auto_commit,
        )

    def get_by_id(self, att_id: SnowflakeID) -> Optional[Dict[str, Any]]:
        """Get attachment by ID."""
        return self._fetch_one(
            "SELECT * FROM msg_attachments WHERE id = ? AND deleted = 0",
            (att_id,),
        )

    def get_by_message(self, message_id: SnowflakeID) -> List[Dict[str, Any]]:
        """Get all attachments for a message."""
        return self._fetch_all(
            "SELECT * FROM msg_attachments WHERE message_id = ? AND deleted = 0",
            (message_id,),
        )

    def get_batch_by_messages(
        self, message_ids: List[SnowflakeID]
    ) -> Dict[SnowflakeID, List[Attachment]]:
        """Get attachments for multiple messages (batch operation)."""
        if not message_ids:
            return {}

        in_clause, params = self._build_in_clause(message_ids)
        rows = self._fetch_all(
            f"SELECT * FROM msg_attachments WHERE message_id IN {in_clause} AND deleted = 0",
            params,
        )

        result: Dict[SnowflakeID, List[Attachment]] = {mid: [] for mid in message_ids}
        for row in rows:
            att = self.row_to_model(row)
            result[att.message_id].append(att)

        return result

    def count_by_message(self, message_id: SnowflakeID) -> int:
        """Count attachments for a message."""
        row = self._fetch_one(
            "SELECT COUNT(*) as cnt FROM msg_attachments WHERE message_id = ? AND deleted = 0",
            (message_id,),
        )
        return row["cnt"] if row else 0

    def soft_delete(self, att_id: SnowflakeID, auto_commit: bool = True) -> None:
        """Soft delete an attachment."""
        self._execute(
            "UPDATE msg_attachments SET deleted = 1 WHERE id = ?",
            (att_id,),
            auto_commit=auto_commit,
        )

    def row_to_model(self, row: Dict[str, Any]) -> Attachment:
        """Convert database row to Attachment model."""
        url = row["url"]

        # Decrypt if encrypted
        if row["url_encrypted"] and url == "[encrypted]":
            try:
                url = decrypt_data(row["url_encrypted"])
            except Exception:
                url = "[decryption failed]"

        return Attachment(
            id=row["id"],
            message_id=row["message_id"],
            filename=row["filename"],
            content_type=row["content_type"],
            size=row["size"],
            url=url,
            url_encrypted=row["url_encrypted"],
            created_at=row["created_at"],
            metadata=self._json_loads(row["metadata"]) if row["metadata"] else None,
            checksum=row.get("checksum"),
            deleted=bool(row["deleted"]),
        )
