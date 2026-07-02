# pyright: reportAttributeAccessIssue=false
"""
File retrieval, deletion, and access-check methods mixed into MediaManager.
"""

import os
import logging
from typing import Optional, Dict, List, Tuple, BinaryIO

from .models import MediaFile
from .exceptions import MediaError, PermissionDeniedError

logger = logging.getLogger(__name__)


class _FilesMixin:
    """File CRUD + access-control methods mixed into MediaManager."""

    # ── single-file retrieval ──────────────────────────────────────────────────

    def get_file(self, file_id: int) -> Optional[MediaFile]:
        row = self._db.fetch_one(
            "SELECT * FROM media_files WHERE id = ? AND deleted = 0", (file_id,)
        )
        if not row:
            return None
        return self._row_to_media_file(row)

    def get_file_by_filename(self, filename: str) -> Optional[MediaFile]:
        row = self._db.fetch_one(
            "SELECT * FROM media_files WHERE filename = ? AND deleted = 0",
            (filename,),
        )
        if not row:
            return None
        return self._row_to_media_file(row)

    # ── batch retrieval ────────────────────────────────────────────────────────

    def get_files_by_filenames(self, filenames: List[str]) -> Dict[str, MediaFile]:
        if not filenames:
            return {}
        placeholders = ",".join("?" for _ in filenames)
        rows = self._db.fetch_all(
            f"SELECT * FROM media_files WHERE filename IN ({placeholders}) "
            f"AND deleted = 0",
            tuple(filenames),
        )
        result = {}
        for row in rows:
            result[row["filename"]] = self._row_to_media_file(row)
        return result

    # ── file data access ───────────────────────────────────────────────────────

    def get_file_data(self, file_id: int) -> Tuple[bytes, str]:
        file = self.get_file(file_id)
        if not file:
            raise MediaError("File not found")
        storage = self._get_storage_by_backend(file.storage_backend.value)
        data = storage.retrieve(file.storage_path)
        return data, file.content_type

    def get_file_stream(self, file_id: int) -> Tuple[BinaryIO, int, str]:
        file = self.get_file(file_id)
        if not file:
            raise MediaError("File not found")
        storage = self._get_storage_by_backend(file.storage_backend.value)
        stream, size = storage.retrieve_stream(file.storage_path)
        return stream, size, file.content_type

    def get_file_stream_optimized(
        self, path: str, content_type: str, backend: str
    ) -> Tuple[BinaryIO, int, str]:
        storage = self._get_storage_by_backend(backend)
        stream, size = storage.retrieve_stream(path)
        return stream, size, content_type

    # ── deletion ───────────────────────────────────────────────────────────────

    def delete_file(self, user_id: int, file_id: int) -> bool:
        file = self.get_file(file_id)
        if not file:
            return False
        if file.uploaded_by != user_id:
            raise PermissionDeniedError("Can only delete own files")
        now = self._get_timestamp()
        self._db.execute(
            "UPDATE media_files SET deleted = 1, deleted_at = ? WHERE id = ?",
            (now, file_id),
        )
        logger.debug(f"File {file_id} deleted by user {user_id}")
        return True

    # ── access control ─────────────────────────────────────────────────────────

    def check_file_access(self, filename: str, user_id: int) -> bool:
        """Check if a user has permission to access a media file.

        Access granted if: original uploader, message-attachment participant,
        public resource (avatar/server icon), or thumbnail of accessible file.
        """
        search_filename = os.path.basename(filename)

        # Thumbnail → delegate to parent file check
        if "thumbnails/" in filename:
            try:
                parts = filename.split("/")
                parent_file_id = parts[-2] if len(parts) >= 2 else None
                if parent_file_id and parent_file_id.isdigit():
                    parent_row = self._db.fetch_one(
                        "SELECT filename FROM media_files WHERE id = ? AND deleted = 0",
                        (int(parent_file_id),),
                    )
                    if parent_row:
                        return self.check_file_access(parent_row["filename"], user_id)
            except Exception as e:
                logger.warning(f"Error checking thumbnail access for {filename}: {e}")

        # 1. Uploader (fast path)
        row = self._db.fetch_one(
            "SELECT id, uploaded_by, checksum FROM media_files "
            "WHERE filename = ? AND deleted = 0",
            (filename,),
        )
        if not row:
            return False

        uploader_id = int(row["uploaded_by"])
        if uploader_id == int(user_id):
            return True

        # 2. Message attachment in a conversation the user participates in
        checksum = row.get("checksum")
        file_id_token = str(row["id"])

        rows = None
        if checksum:
            rows = self._db.fetch_all(
                """SELECT m.conversation_id
                   FROM msg_messages m
                   JOIN msg_attachments a ON m.id = a.message_id
                   WHERE (
                       a.checksum = ?
                       OR a.metadata LIKE '%' || ? || '%'
                       OR a.metadata LIKE '%' || ? || '%'
                       OR a.metadata LIKE '%' || ? || '%'
                       OR a.metadata LIKE '%' || ? || '%'
                       OR a.metadata LIKE '%' || ? || '%'
                       OR a.metadata LIKE '%' || ? || '%'
                   ) AND a.deleted = 0""",
                (
                    checksum,
                    f'"media_file_id": {file_id_token}',
                    f'"media_file_id": "{file_id_token}"',
                    f'"media_file_id":"{file_id_token}"',
                    f'"file_id": {file_id_token}',
                    f'"file_id": "{file_id_token}"',
                    f'"file_id":"{file_id_token}"',
                ),
            )

        if not rows:
            rows = self._db.fetch_all(
                """SELECT m.conversation_id
                   FROM msg_messages m
                   JOIN msg_attachments a ON m.id = a.message_id
                   WHERE (
                       a.filename = ?
                       OR a.url LIKE '%' || ?
                       OR a.metadata LIKE '%' || ? || '%'
                       OR a.metadata LIKE '%' || ? || '%'
                       OR a.metadata LIKE '%' || ? || '%'
                       OR a.metadata LIKE '%' || ? || '%'
                       OR a.metadata LIKE '%' || ? || '%'
                       OR a.metadata LIKE '%' || ? || '%'
                   ) AND a.deleted = 0""",
                (
                    search_filename,
                    search_filename,
                    f'"media_file_id": {file_id_token}',
                    f'"media_file_id": "{file_id_token}"',
                    f'"media_file_id":"{file_id_token}"',
                    f'"file_id": {file_id_token}',
                    f'"file_id": "{file_id_token}"',
                    f'"file_id":"{file_id_token}"',
                ),
            )

        if rows and self._messaging:
            for r in rows:
                conv_id = r["conversation_id"]
                try:
                    manager = (
                        self._messaging
                        if hasattr(self._messaging, "is_participant")
                        else self._messaging.get_manager()
                    )
                    if manager.is_participant(conv_id, user_id):
                        return True
                except Exception as e:
                    logger.debug(
                        f"Participant check failed for conversation {conv_id}: {e}"
                    )

        # 3. Public resources (avatar / server icon)
        avatar_row = self._db.fetch_one(
            "SELECT 1 FROM auth_users WHERE avatar_url LIKE '%' || ?",
            (search_filename,),
        )
        if avatar_row:
            return True

        icon_row = self._db.fetch_one(
            "SELECT 1 FROM srv_servers s JOIN srv_members m "
            "ON s.id = m.server_id "
            "WHERE s.icon_url LIKE '%' || ? AND m.user_id = ? AND s.deleted = 0",
            (search_filename, user_id),
        )
        if icon_row:
            return True

        return False
