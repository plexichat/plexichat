# pyright: reportAttributeAccessIssue=false
"""
File retrieval, deletion, and access-check methods mixed into MediaManager.
"""

import os
import time
import logging
from typing import Any, Optional, Dict, List, Tuple, BinaryIO

from .models import MediaFile
from .exceptions import MediaError, PermissionDeniedError
from ._file_meta_cache import FileMeta, FileMetaCache
from src.utils.encryption import decrypt_data

logger = logging.getLogger(__name__)

# Retry window for transient SQLite write-lock contention (see
# ``_retry_on_db_lock``). Background upload tasks and synchronous deletes can
# briefly contend for the single WAL write lock; a short bounded retry is safe
# because those tasks commit within microseconds and never deadlock.
_LOCK_RETRY_ATTEMPTS = 6
_LOCK_RETRY_BASE_DELAY_S = 0.02

# Erasing a user with many deduplicated uploads used to issue one
# ``SELECT COUNT(*) ... uploaded_by != ?`` per distinct checksum (an N+1).
# Resolve all of them in bounded ``IN`` chunks instead. 400 checksum binds plus
# the user id stay well under the shared 900-bind ceiling of the execution
# layer, so an arbitrarily large erasure degrades to a handful of queries.
_SHARED_CHECKSUM_CHUNK = 400


def _is_db_lock_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return "locked" in msg or "busy" in msg


def _is_local_media_reference(url: Any, filename: str) -> bool:
    """Return True only for a same-origin path that ends in ``filename``.

    ``check_file_access`` used to treat any stored avatar/icon URL whose text
    ended with a media filename as a public reference to that file. Because
    those columns can hold attacker-chosen absolute URLs, the suffix match
    let a caller "publish" somebody else's private attachment by putting its
    filename in their own server icon URL. Canonical icon/avatar URLs are
    local API paths (e.g. ``/api/v1/avatars/servers/123``), so restricting the
    grant to local, path-only references preserves legitimate values while
    removing the cross-user file-access bypass.
    """
    if not isinstance(url, str) or not filename:
        return False
    candidate = url.strip()
    # Only a local absolute path is trusted. Remote URLs (http/https and
    # protocol-relative ``//host/...``) must never unlock a private file.
    if not candidate.startswith("/") or candidate.startswith("//"):
        return False
    if any(ch in candidate for ch in ("://", "\\", "?", "#", "\x00")):
        return False
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in candidate):
        return False
    segments = [part for part in candidate.split("/") if part]
    if not segments or ".." in segments or "." in segments:
        return False
    return segments[-1] == filename


class _FilesMixin:
    """File CRUD + access-control methods mixed into MediaManager."""

    # ── stable-metadata cache helpers ──────────────────────────────────────────
    #
    # The cache is lazily created so managers built via ``__new__`` by the
    # tuple-row tests remain valid. It holds only id/filename/uploaded_by/
    # checksum; scan_status and deleted are always read fresh.

    def _file_meta(self) -> FileMetaCache:
        cache = getattr(self, "_file_meta_cache", None)
        if cache is None:
            config = getattr(self, "_config", None) or {}
            try:
                size = int(config.get("file_meta_cache_size", 256))
            except (TypeError, ValueError):
                size = 256
            cache = FileMetaCache(maxsize=size)
            self._file_meta_cache = cache
        return cache

    @staticmethod
    def _meta_from_file(file: MediaFile) -> FileMeta:
        return FileMeta(
            id=int(file.id),
            filename=str(file.filename),
            uploaded_by=int(file.uploaded_by),
            checksum=file.checksum,
        )

    def _cache_meta_from_file(self, file: Optional[MediaFile]) -> None:
        if file is not None and getattr(file, "id", None) is not None:
            self._file_meta().put(self._meta_from_file(file))

    def _prime_file_meta(
        self, file_id: int, filename: str, uploaded_by: int, checksum: Optional[str]
    ) -> None:
        """Invalidate any prior entry for this filename, then prime the cache.

        Called straight after an INSERT so the upload response's own access
        check (and later reads in the same request) cost no extra DB query.
        Invalidating first prevents a reused filename from leaving a stale
        entry for an older file behind.
        """
        name = str(filename)
        cache = self._file_meta()
        cache.invalidate(filename=name)
        cache.put(
            FileMeta(
                id=int(file_id),
                filename=name,
                uploaded_by=int(uploaded_by),
                checksum=checksum,
            )
        )

    def _invalidate_file_meta(
        self, file_id: Optional[int] = None, filename: Optional[str] = None
    ) -> None:
        self._file_meta().invalidate(file_id=file_id, filename=filename)

    @staticmethod
    def _meta_from_access_row(row, filename: str) -> FileMeta:
        """Parse ``SELECT id, uploaded_by, checksum`` (mapping or tuple row)."""
        if isinstance(row, dict):
            return FileMeta(
                id=int(row["id"]),
                filename=str(filename),
                uploaded_by=int(row["uploaded_by"]),
                checksum=row.get("checksum"),
            )
        # Tuple adapters return columns in SELECT order: id, uploaded_by, checksum.
        return FileMeta(
            id=int(row[0]),
            filename=str(filename),
            uploaded_by=int(row[1]),
            checksum=row[2] if len(row) > 2 else None,
        )

    # ── single-file retrieval ──────────────────────────────────────────────────

    def get_file(self, file_id: int) -> Optional[MediaFile]:
        row = self._db.fetch_one(
            "SELECT * FROM media_files WHERE id = ? AND deleted = 0", (file_id,)
        )
        if not row:
            self._invalidate_file_meta(file_id=file_id)
            return None
        file = self._row_to_media_file(row)
        self._cache_meta_from_file(file)
        return file

    def get_file_by_filename(self, filename: str) -> Optional[MediaFile]:
        row = self._db.fetch_one(
            "SELECT * FROM media_files WHERE filename = ? AND deleted = 0",
            (filename,),
        )
        if not row:
            self._invalidate_file_meta(filename=filename)
            return None
        file = self._row_to_media_file(row)
        self._cache_meta_from_file(file)
        return file

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
            # ``fetch_all`` may return mapping or tuple rows (see
            # docs/admin-hardening.md); ``SELECT *`` follows the
            # ``media_files`` schema order, where ``filename`` is second.
            key = row["filename"] if isinstance(row, dict) else row[1]
            file = self._row_to_media_file(row)
            result[key] = file
            self._cache_meta_from_file(file)
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
        self._invalidate_file_meta(file_id=file_id, filename=file.filename)
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
        # ``SELECT COUNT(*) AS count`` yields a single-column row; read it by
        # position when the adapter returns tuples instead of mappings.
        if (
            attachment_count
            and int(
                attachment_count["count"]
                if isinstance(attachment_count, dict)
                else attachment_count[0]
            )
            > 0
        ):
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
                encrypted_url = decrypt_data(
                    attachment["url_encrypted"]
                    if isinstance(attachment, dict)
                    else attachment[0]
                )
            except Exception as decrypt_error:
                logger.debug(
                    f"skipping corrupted attachment reference: {decrypt_error}"
                )
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
        if (
            avatar_count
            and int(
                avatar_count["count"]
                if isinstance(avatar_count, dict)
                else avatar_count[0]
            )
            > 0
        ):
            return False
        icon_count = self._db.fetch_one(
            "SELECT COUNT(*) AS count FROM srv_servers WHERE icon_url LIKE ? AND deleted = 0",
            (f"%{file.filename}",),
        )
        if (
            icon_count
            and int(
                icon_count["count"] if isinstance(icon_count, dict) else icon_count[0]
            )
            > 0
        ):
            return False
        stash_item_count = self._db.fetch_one(
            "SELECT COUNT(*) AS count FROM stash_items "
            "WHERE deleted = 0 AND media_object_id IN (?, ?)",
            (file_token, file.filename),
        )
        if (
            stash_item_count
            and int(
                stash_item_count["count"]
                if isinstance(stash_item_count, dict)
                else stash_item_count[0]
            )
            > 0
        ):
            return False
        stash_version_count = self._db.fetch_one(
            "SELECT COUNT(*) AS count FROM stash_versions v "
            "JOIN stash_items i ON i.id = v.item_id "
            "WHERE i.deleted = 0 AND v.media_object_id IN (?, ?)",
            (file_token, file.filename),
        )
        if (
            stash_version_count
            and int(
                stash_version_count["count"]
                if isinstance(stash_version_count, dict)
                else stash_version_count[0]
            )
            > 0
        ):
            return False
        artifact_count = self._db.fetch_one(
            "SELECT COUNT(*) AS count FROM artifacts "
            "WHERE COALESCE(payload, '') LIKE ? OR COALESCE(payload, '') LIKE ?",
            (f"%{file_token}%", f"%{file.filename}%"),
        )
        if (
            artifact_count
            and int(
                artifact_count["count"]
                if isinstance(artifact_count, dict)
                else artifact_count[0]
            )
            > 0
        ):
            return False
        dedup_count = None
        if checksum and self._db.table_exists("media_file_hashes"):
            dedup_count = self._db.fetch_one(
                "SELECT reference_count FROM media_file_hashes WHERE hash_value = ?",
                (checksum,),
            )
        if (
            dedup_count
            and int(
                dedup_count["reference_count"]
                if isinstance(dedup_count, dict)
                else dedup_count[0]
            )
            > 1
        ):
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
            except Exception as rollback_error:
                # The BaseException that got us here is the one to propagate.
                logger.debug(f"media transaction rollback failed: {rollback_error}")
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
            # The tombstone transaction has already set deleted = 1, so the
            # cached identity metadata must not grant further access even if
            # the subsequent storage removal fails.
            self._invalidate_file_meta(file_id=file_id, filename=file.filename)
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

    def _shared_checksums(self, checksums, user_id: int) -> set:
        """Return the checksums in ``checksums`` that another user also owns.

        One bounded ``IN`` query per chunk replaces the old per-checksum
        ``COUNT(*)`` probe. Only checksum *values* are read back, and only for
        checksums the erasing user already owns, so no other user's file
        metadata can leak into this path. The result is used solely to decide
        whether the erasing user's own storage object may be removed; it is a
        fresh read (never cached), so a concurrent upload on another worker is
        always observed before an object is deleted.
        """
        unique = {str(value) for value in checksums if value}
        if not unique:
            return set()
        ordered = list(unique)
        shared: set = set()
        for start in range(0, len(ordered), _SHARED_CHECKSUM_CHUNK):
            chunk = ordered[start : start + _SHARED_CHECKSUM_CHUNK]
            placeholders = ",".join("?" for _ in chunk)
            rows = self._db.fetch_all(
                "SELECT DISTINCT checksum FROM media_files "
                f"WHERE uploaded_by != ? AND checksum IN ({placeholders})",
                (int(user_id), *chunk),
            )
            for row in rows:
                value = row["checksum"] if isinstance(row, dict) else row[0]
                if value in unique:
                    shared.add(str(value))
        return shared

    def delete_files_for_user(self, user_id: int) -> List[str]:
        """Erase every file a user uploaded, including storage objects.

        Account erasure cannot go through ``delete_file_permanently``: that
        defers while any reference remains, and a file whose content was
        deduplicated against the same user's other uploads keeps a reference
        count above one, so every single call would be refused and the account
        would be left owning its media for ever. The user's files are grouped
        by checksum here, so the dedup counter is released once per row and the
        object is removed only when no other user's file shares it -- the
        metadata of this user's copy still goes in that case.

        Returns the storage paths that could not be removed, for the caller to
        record on its audit event.
        """
        uid = int(user_id)
        rows = [
            dict(row)
            for row in self._db.fetch_all(
                "SELECT id, checksum, storage_backend, storage_path "
                "FROM media_files WHERE uploaded_by = ? ORDER BY id",
                (uid,),
            )
        ]
        groups: Dict[Optional[str], List[Dict[str, Any]]] = {}
        for row in rows:
            groups.setdefault(row.get("checksum"), []).append(row)

        # Resolve every distinct checksum in bounded chunks instead of one
        # COUNT query per checksum. A missing/failed lookup is not silently
        # treated as "unique": fetch_all either returns authoritative rows or
        # raises, so an error can never justify deleting a shared object.
        shared_checksums = self._shared_checksums(groups.keys(), uid)

        unswept: List[str] = []
        for checksum, files in groups.items():
            shared = bool(checksum) and str(checksum) in shared_checksums

            deleted_paths: set = set()
            for file_row in files:
                file_id = int(file_row["id"])
                backend = str(file_row.get("storage_backend") or "s3")
                path = file_row.get("storage_path")
                if not shared and path and path not in deleted_paths:
                    try:
                        storage = self._get_storage_by_backend(backend)
                        if storage.exists(path) and not storage.delete(path):
                            unswept.append(str(path))
                    except Exception as exc:
                        logger.warning(
                            "Erase: media object %s could not be deleted: %s",
                            path,
                            exc,
                        )
                        unswept.append(str(path))
                    deleted_paths.add(path)

                self._db.execute(
                    "DELETE FROM media_file_deletion_locks WHERE media_file_id = ?",
                    (file_id,),
                )
                self._db.execute(
                    "DELETE FROM media_file_deletion_tombstones "
                    "WHERE media_file_id = ?",
                    (file_id,),
                )
                self._db.execute("DELETE FROM media_files WHERE id = ?", (file_id,))
                self._invalidate_file_meta(file_id=file_id)
                if checksum and self._dedup_manager is not None:
                    self._dedup_manager.decrement_reference(str(checksum))

        if rows:
            logger.info("Erased %d media file(s) for user %s", len(rows), uid)
        return unswept

    # ── access control ─────────────────────────────────────────────────────────

    def check_file_access(self, filename: str, user_id: int) -> bool:
        """Check if a user has permission to access a media file.

        Access granted if: original uploader, message-attachment participant,
        public resource (avatar/server icon), or thumbnail of accessible file.
        """
        search_filename = os.path.basename(filename)

        cache = self._file_meta()

        # Thumbnail → delegate to parent file check
        if "thumbnails/" in filename:
            try:
                parts = filename.split("/")
                parent_file_id = parts[-2] if len(parts) >= 2 else None
                if parent_file_id and parent_file_id.isdigit():
                    parent_id = int(parent_file_id)
                    # A resolve-get earlier in this request already cached the
                    # parent's stable metadata: skip both the parent filename
                    # lookup and the recursive one below.
                    cached_parent = cache.get_by_id(parent_id)
                    if cached_parent is not None:
                        return self.check_file_access(cached_parent.filename, user_id)
                    parent_row = self._db.fetch_one(
                        "SELECT filename FROM media_files WHERE id = ? AND deleted = 0",
                        (parent_id,),
                    )
                    if parent_row:
                        parent_filename = (
                            parent_row["filename"]
                            if isinstance(parent_row, dict)
                            else parent_row[0]
                        )
                        return self.check_file_access(parent_filename, user_id)
            except Exception as e:
                logger.warning(f"Error checking thumbnail access for {filename}: {e}")

        # 1. Uploader (fast path). The identity triple is stable for the life
        # of a row, so a prior resolve of this file in the same request makes
        # this lookup free. ``deleted`` is never cached; upload and every
        # deletion path invalidate the entry, and a miss below re-queries.
        meta = cache.get_by_filename(filename)
        if meta is None:
            row = self._db.fetch_one(
                "SELECT id, uploaded_by, checksum FROM media_files "
                "WHERE filename = ? AND deleted = 0",
                (filename,),
            )
            if not row:
                cache.invalidate(filename=filename)
                return False
            meta = self._meta_from_access_row(row, filename)
            cache.put(meta)

        uploader_id = int(meta.uploaded_by)
        if uploader_id == int(user_id):
            return True

        # 2. Message attachment in a conversation the user participates in.
        # SECURITY: every link field (checksum, filename, url, file_id in
        # metadata) is client-supplied. Require that the referencing
        # message was authored by the file's uploader -- the only party
        # with inherent authority to share it. Forged references fail
        # closed while legitimate upload-then-attach flows still work.
        checksum = meta.checksum
        file_id_token = str(meta.id)

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
                   ) AND a.deleted = 0 AND m.author_id = ?""",
                (
                    checksum,
                    f'"media_file_id": {file_id_token}',
                    f'"media_file_id": "{file_id_token}"',
                    f'"media_file_id":"{file_id_token}"',
                    f'"file_id": {file_id_token}',
                    f'"file_id": "{file_id_token}"',
                    f'"file_id":"{file_id_token}"',
                    uploader_id,
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
                   ) AND a.deleted = 0 AND m.author_id = ?""",
                (
                    search_filename,
                    search_filename,
                    f'"media_file_id": {file_id_token}',
                    f'"media_file_id": "{file_id_token}"',
                    f'"media_file_id":"{file_id_token}"',
                    f'"file_id": {file_id_token}',
                    f'"file_id": "{file_id_token}"',
                    f'"file_id":"{file_id_token}"',
                    uploader_id,
                ),
            )

        if rows and self._messaging:
            for r in rows:
                # ``SELECT m.conversation_id`` -- single column, position 0
                # for tuple-row adapters.
                conv_id = r["conversation_id"] if isinstance(r, dict) else r[0]
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

        # 3. Public resources (avatar / server icon).
        # SECURITY: a bare suffix match here was an access-control bypass.
        # Server ``icon_url`` is an absolute http(s) URL accepted from any
        # server owner (ServerCreate/UpdateRequest), so an owner could point it
        # at a *private* attachment's filename and this query would grant every
        # member of that server access to the file. Avatar URLs have been
        # abused the same way by flows that store external URLs. Only a local,
        # path-only reference that names the exact file may confer access.
        avatar_rows = self._db.fetch_all(
            "SELECT avatar_url FROM auth_users WHERE avatar_url LIKE '%' || ?",
            (search_filename,),
        )
        if any(
            _is_local_media_reference(
                row["avatar_url"] if isinstance(row, dict) else row[0], search_filename
            )
            for row in (avatar_rows or [])
        ):
            return True

        icon_rows = self._db.fetch_all(
            "SELECT s.icon_url, s.owner_id FROM srv_servers s JOIN srv_members m "
            "ON s.id = m.server_id "
            "WHERE s.icon_url LIKE '%' || ? AND m.user_id = ? AND s.deleted = 0",
            (search_filename, user_id),
        )
        for row in icon_rows or ():
            icon_url = row["icon_url"] if isinstance(row, dict) else row[0]
            owner_id = row["owner_id"] if isinstance(row, dict) else row[1]
            # icon_url is client-controllable on server create/update and the
            # core validator accepts ANY same-origin path. A bare filename
            # match therefore let a server owner point their icon at somebody
            # else's private attachment and grant every member a download on
            # the strength of server membership alone. Only treat a local
            # reference as public when the referenced file was uploaded by the
            # server owner; every other owner fails closed.
            if (
                _is_local_media_reference(icon_url, search_filename)
                and owner_id is not None
                and int(owner_id) == uploader_id
            ):
                return True

        return False
