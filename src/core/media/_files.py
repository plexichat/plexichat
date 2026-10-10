# pyright: reportAttributeAccessIssue=false
"""
File retrieval, deletion, and access-check methods mixed into MediaManager.
"""

import os
import time
import logging
from typing import Optional, Dict, List, Tuple, BinaryIO

from .models import MediaFile
from .exceptions import MediaError, PermissionDeniedError
from src.utils.encryption import decrypt_data

logger = logging.getLogger(__name__)

# Retry window for transient SQLite write-lock contention (see
# ``_retry_on_db_lock``). Background upload tasks and synchronous deletes can
# briefly contend for the single WAL write lock; a short bounded retry is safe
# because those tasks commit within microseconds and never deadlock.
_LOCK_RETRY_ATTEMPTS = 6
_LOCK_RETRY_BASE_DELAY_S = 0.02


def _is_db_lock_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return "locked" in msg or "busy" in msg


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

    def can_delete_file_permanently(self, file_id: int) -> bool:
        """Return whether the media boundary can safely remove a file.

        This is deliberately conservative. A media file may be referenced by
        message attachments, avatars, server icons, or deduplicated content
        outside the caller's domain. Callers must treat ``False`` as a
        retention/deletion deferral, never as permission to remove metadata.
        """
        file = self._get_file_for_permanent_delete(file_id)
        if not file:
            return True
        # A missing reference-owner table means the schema is older or
        # incomplete. Refuse permanent deletion rather than treating unknown
        # references as absent.
        required_tables = (
            "msg_attachments",
            "stash_items",
            "stash_versions",
            "auth_users",
            "srv_servers",
            "artifacts",
            "media_file_deletion_tombstones",
        )
        if not all(self._db.table_exists(table) for table in required_tables):
            return False
        checksum = file.checksum
        file_token = str(file.id)
        attachment_count = self._db.fetch_one(
            "SELECT COUNT(*) AS count FROM msg_attachments WHERE deleted = 0 "
            "AND ((checksum IS NOT NULL AND checksum = ?) OR "
            "filename = ? OR url LIKE ? OR "
            "metadata LIKE ? OR metadata LIKE ? OR metadata LIKE ? OR "
            "metadata LIKE ? OR metadata LIKE ? OR metadata LIKE ? OR "
            "metadata LIKE ? OR metadata LIKE ?)",
            (
                checksum or "",
                file.filename,
                f"%{file.filename}",
                f'%"media_file_id": {file_token}%',
                f'%"media_file_id": "{file_token}"%',
                f'%"media_file_id":{file_token}%',
                f'%"media_file_id":"{file_token}"%',
                f'%"file_id": {file_token}%',
                f'%"file_id": "{file_token}"%',
                f'%"file_id":{file_token}%',
                f'%"file_id":"{file_token}"%',
            ),
        )
        if attachment_count and int(attachment_count["count"]) > 0:
            return False
        # Legacy encrypted attachment rows may predate canonical file_id
        # metadata. Resolve them at the application boundary so deletion is
        # target-specific rather than treating every encrypted row as a match.
        encrypted_attachments = self._db.fetch_all(
            "SELECT url_encrypted FROM msg_attachments "
            "WHERE deleted = 0 AND url_encrypted IS NOT NULL"
        )
        for attachment in encrypted_attachments:
            try:
                encrypted_url = decrypt_data(attachment["url_encrypted"])
            except Exception:
                # Corrupted encrypted reference — skip rather than blocking all
                # deletions.  The original ``return False`` meant a single bad
                # row prevented *any* file from ever being deleted.
                continue
            if file.filename in str(encrypted_url):
                return False
        avatar_count = self._db.fetch_one(
            "SELECT COUNT(*) AS count FROM auth_users WHERE avatar_url LIKE ?",
            (f"%{file.filename}",),
        )
        if avatar_count and int(avatar_count["count"]) > 0:
            return False
        icon_count = self._db.fetch_one(
            "SELECT COUNT(*) AS count FROM srv_servers WHERE icon_url LIKE ? AND deleted = 0",
            (f"%{file.filename}",),
        )
        if icon_count and int(icon_count["count"]) > 0:
            return False
        stash_item_count = self._db.fetch_one(
            "SELECT COUNT(*) AS count FROM stash_items "
            "WHERE deleted = 0 AND media_object_id IN (?, ?)",
            (file_token, file.filename),
        )
        if stash_item_count and int(stash_item_count["count"]) > 0:
            return False
        stash_version_count = self._db.fetch_one(
            "SELECT COUNT(*) AS count FROM stash_versions v "
            "JOIN stash_items i ON i.id = v.item_id "
            "WHERE i.deleted = 0 AND v.media_object_id IN (?, ?)",
            (file_token, file.filename),
        )
        if stash_version_count and int(stash_version_count["count"]) > 0:
            return False
        artifact_count = self._db.fetch_one(
            "SELECT COUNT(*) AS count FROM artifacts "
            "WHERE COALESCE(payload, '') LIKE ? OR COALESCE(payload, '') LIKE ?",
            (f"%{file_token}%", f"%{file.filename}%"),
        )
        if artifact_count and int(artifact_count["count"]) > 0:
            return False
        dedup_count = None
        if checksum and self._db.table_exists("media_file_hashes"):
            dedup_count = self._db.fetch_one(
                "SELECT reference_count FROM media_file_hashes WHERE hash_value = ?",
                (checksum,),
            )
        if dedup_count and int(dedup_count["reference_count"]) > 1:
            return False
        return True

    def _get_file_for_permanent_delete(self, file_id: int) -> Optional[MediaFile]:
        """Load an active or soft-deleted file for an internal purge."""
        row = self._db.fetch_one("SELECT * FROM media_files WHERE id = ?", (file_id,))
        if not row:
            return None
        return self._row_to_media_file(row)

    def get_file_by_filename_for_permanent_delete(
        self, filename: str
    ) -> Optional[MediaFile]:
        """Load an active or soft-deleted file by opaque filename."""
        row = self._db.fetch_one(
            "SELECT * FROM media_files WHERE filename = ?", (filename,)
        )
        if not row:
            return None
        return self._row_to_media_file(row)

    def _run_transaction(self, begin, commit, rollback, body) -> None:
        """Execute ``body`` inside one transaction, rolling back on any error."""
        begin()
        try:
            body()
            commit()
        except BaseException:
            try:
                rollback()
            except Exception:
                pass
            raise

    def _retry_on_db_lock(self, fn) -> None:
        """Run ``fn``, retrying transient SQLite write-lock contention.

        ``delete_file_permanently`` removes a media row inside an explicit
        transaction while the fire-and-forget tasks spawned by ``upload_file``
        (malware scan, thumbnails, pHash dedup, dedup registration) can still
        be writing to the same database. In WAL mode only one writer may hold
        the lock at a time, so the delete's write can transiently fail with
        ``database is locked``. The ``execute`` layer deliberately does not
        retry inside transactions (re-running one statement could double-apply
        it), so we retry the whole transaction instead: those background tasks
        commit within microseconds and never deadlock, making a short bounded
        retry safe and correct.
        """
        for attempt in range(_LOCK_RETRY_ATTEMPTS):
            try:
                fn()
                return
            except Exception as exc:
                if not _is_db_lock_error(exc) or attempt == _LOCK_RETRY_ATTEMPTS - 1:
                    raise
                time.sleep(_LOCK_RETRY_BASE_DELAY_S * (2**attempt))

    def delete_file_permanently(self, user_id: int, file_id: int) -> bool:
        """Remove an owned, unreferenced media record and its storage object."""
        file = self._get_file_for_permanent_delete(file_id)
        if not file:
            return False
        if int(file.uploaded_by) != int(user_id):
            raise PermissionDeniedError("Can only permanently delete own files")

        begin = getattr(self._db, "begin_transaction", None)
        commit = getattr(self._db, "commit", None)
        rollback = getattr(self._db, "rollback", None)
        if not callable(begin) or not callable(commit) or not callable(rollback):
            raise MediaError("Media deletion requires transactional database support")

        storage = self._get_storage_by_backend(file.storage_backend.value)
        deletion_timestamp = self._get_timestamp()

        # Phase one durably publishes the tombstone before touching object
        # storage. If the worker dies after this commit, every guarded writer
        # continues to reject the reference and a later cleanup can retry the
        # storage removal safely.
        def _publish_tombstone() -> None:
            def _body() -> None:
                lock_cursor = self._db.execute(
                    "UPDATE media_files SET deleted = 1, deleted_at = ? WHERE id = ?",
                    (deletion_timestamp, int(file_id)),
                    auto_commit=False,
                )
                try:
                    if int(getattr(lock_cursor, "rowcount", 0)) != 1:
                        raise MediaError("Media object is unavailable")
                finally:
                    lock_cursor.close()
                lock_cursor = self._db.execute(
                    "INSERT INTO media_file_deletion_locks "
                    "(media_file_id, requested_by, requested_at) VALUES (?, ?, ?) "
                    "ON CONFLICT(media_file_id) DO NOTHING",
                    (int(file_id), int(user_id), deletion_timestamp),
                    auto_commit=False,
                )
                lock_cursor.close()
                tombstone_cursor = self._db.execute(
                    "INSERT INTO media_file_deletion_tombstones "
                    "(media_file_id, filename, requested_by, requested_at) "
                    "VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(media_file_id) DO NOTHING",
                    (int(file_id), file.filename, int(user_id), deletion_timestamp),
                    auto_commit=False,
                )
                tombstone_cursor.close()
                if not self.can_delete_file_permanently(file_id):
                    raise MediaError("Media object is still referenced")

            self._run_transaction(begin, commit, rollback, _body)

        try:
            self._retry_on_db_lock(_publish_tombstone)
        except MediaError:
            raise
        except Exception as exc:
            raise MediaError("Could not publish media deletion tombstone") from exc

        try:
            if not storage.delete(file.storage_path):
                raise MediaError("Underlying media object could not be deleted")
        except Exception as exc:
            # Leave the durable tombstone and soft-deleted metadata in place;
            # cleanup can retry without exposing a newly-created reference.
            if isinstance(exc, MediaError):
                raise
            raise MediaError("Underlying media object could not be deleted") from exc

        def _remove_metadata() -> None:
            def _body() -> None:
                # Delete the metadata rows first so the DELETE is the first
                # write of the transaction and acquires the WAL write lock up
                # front. ``decrement_reference`` opens with a SELECT; if that
                # read ran first, the subsequent write would be a
                # read-snapshot -> write upgrade that fails immediately with
                # ``database is locked`` (SQLite does not run the busy handler
                # for a stale-snapshot upgrade) whenever a background upload
                # task briefly holds the write lock.
                self._db.execute(
                    "DELETE FROM media_files WHERE id = ?",
                    (int(file_id),),
                    auto_commit=False,
                )
                self._db.execute(
                    "DELETE FROM media_file_deletion_locks WHERE media_file_id = ?",
                    (int(file_id),),
                    auto_commit=False,
                )
                if file.checksum and self._dedup_manager is not None:
                    self._dedup_manager.decrement_reference(file.checksum)

            self._run_transaction(begin, commit, rollback, _body)

        try:
            self._retry_on_db_lock(_remove_metadata)
        except Exception as exc:
            # The object is already gone, so retain the tombstone and leave
            # metadata for an idempotent repair job rather than resurrecting a
            # reference to missing storage.
            raise MediaError(
                "Media metadata deletion failed after storage removal"
            ) from exc

        logger.info("Permanently deleted media file %s", file_id)
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
