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
        metadata = self._reject_locked_media_reference(
            metadata, checksum, filename, url, url_encrypted
        )
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

    def create_bulk(
        self,
        attachments: List[Dict[str, Any]],
        auto_commit: bool = True,
    ) -> None:
        """Create multiple attachments in batch."""
        if not attachments:
            return
        normalized_attachments = []
        for attachment in attachments:
            normalized = dict(attachment)
            normalized["metadata"] = self._reject_locked_media_reference(
                attachment.get("metadata"),
                attachment.get("checksum"),
                attachment.get("filename"),
                attachment.get("url"),
                attachment.get("url_encrypted"),
            )
            normalized_attachments.append(normalized)
        # Only start transaction if not already in one
        in_trans = getattr(self._db, "in_transaction", False)
        if not in_trans:
            self.begin_transaction()

        try:
            for att in normalized_attachments:
                self._execute(
                    """INSERT INTO msg_attachments 
                       (id, message_id, filename, content_type, size, url, url_encrypted, created_at, metadata, checksum)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        att["id"],
                        att["message_id"],
                        att["filename"],
                        att["content_type"],
                        att["size"],
                        att["url"],
                        att.get("url_encrypted"),
                        att["created_at"],
                        self._json_dumps(att.get("metadata")),
                        att.get("checksum"),
                    ),
                    auto_commit=False,
                )
            if auto_commit and not in_trans:
                self.commit()
        except Exception:
            if not in_trans:
                self.rollback()
            raise

    def _reject_locked_media_reference(
        self,
        metadata: Optional[Dict[str, Any]],
        checksum: Optional[str],
        filename: Optional[str] = None,
        url: Optional[str] = None,
        url_encrypted: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Reject and canonicalize attachment media references.

        Media uploads already expose ``file_id`` metadata. Preserve that
        canonical reference when callers only provide a URL (including an
        encrypted URL), so later deletion checks do not depend on decrypting
        arbitrary historical ciphertext in SQL.
        """
        candidates = []
        explicit_ids = set()
        canonical_metadata = dict(metadata) if isinstance(metadata, dict) else {}
        if isinstance(metadata, dict):
            for key in ("media_file_id", "file_id"):
                value = metadata.get(key)
                if value is not None and str(value).isdigit():
                    explicit_ids.add(int(value))
                    candidates.append(int(value))
        if not candidates and checksum:
            row = self._db.fetch_one(
                "SELECT id FROM media_files WHERE checksum = ? AND deleted = 0",
                (checksum,),
            )
            if row:
                candidates.append(int(row["id"]))
        if not candidates and filename:
            rows = self._db.fetch_all(
                "SELECT id FROM media_files WHERE filename = ? AND deleted = 0",
                (str(filename),),
            )
            candidates.extend(int(row["id"]) for row in rows)
        candidate_urls = [str(url)] if url else []
        if url_encrypted:
            try:
                decrypted_url = decrypt_data(url_encrypted)
            except Exception as exc:
                raise ValueError("Encrypted attachment URL is invalid") from exc
            candidate_urls.append(str(decrypted_url))
        for candidate_url in candidate_urls:
            rows = self._db.fetch_all(
                "SELECT id, filename FROM media_files WHERE ? LIKE '%' || filename",
                (candidate_url,),
            )
            candidates.extend(int(row["id"]) for row in rows)
            if self._db.table_exists("media_file_deletion_tombstones"):
                tombstones = self._db.fetch_all(
                    "SELECT media_file_id FROM media_file_deletion_tombstones "
                    "WHERE ? LIKE '%' || filename",
                    (candidate_url,),
                )
                candidates.extend(int(row["media_file_id"]) for row in tombstones)
        distinct_candidates = set(candidates)
        if explicit_ids and not explicit_ids.issuperset(distinct_candidates):
            raise ValueError("Attachment media references do not agree")
        for file_id in distinct_candidates:
            # Reference writers participate in the same row-lock protocol as
            # permanent deletion. PostgreSQL blocks here until the deleter
            # commits/rolls back; SQLite's writer lock provides the equivalent
            # serialization. The lock-table check then handles the committed
            # tombstone for both drivers.
            media_query = "SELECT deleted FROM media_files WHERE id = ?"
            if getattr(self._db, "type", "sqlite") == "postgres":
                media_query += " FOR UPDATE"
            media_row = self._db.fetch_one(media_query, (file_id,))
            if not media_row or bool(media_row.get("deleted", 0)):
                raise ValueError("Media object is unavailable")
            canonical_metadata.setdefault("file_id", int(file_id))
            locked = self._db.fetch_one(
                "SELECT 1 FROM media_file_deletion_locks WHERE media_file_id = ?",
                (file_id,),
            )
            if locked:
                raise ValueError("Media object is being permanently deleted")
        return canonical_metadata or None

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
