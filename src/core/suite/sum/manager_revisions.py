"""Sum manager mixin: durable op log, revision blobs, sqlite helpers (split from manager.py)."""

import hashlib
import os
import sqlite3
import tempfile
import uuid

from typing import Any, Callable, Dict, List, Optional, Tuple

from .schema import (
    PLEXISUM_FORMAT_VERSION,
    ensure_feature_tables,
)
from .manager_protocol import SumManagerProtocol
from ._shared import (
    REVISION_KEEP_COUNT,
    SumConflictError,
    SumNotFoundError,
    SumStorageError,
)


class SumManagerRevisions(SumManagerProtocol):
    def _append_op_durable(
        self,
        workbook_id: int,
        operation_id: str,
        user_id: int,
        base_revision: int,
        revision: int,
        operation_type: str,
        sheet_id: int,
        cells: List[Dict[str, Any]],
        now: int,
        conflict_message: str,
    ) -> None:
        """Durably record an operation and advance the revision counter.

        Both happen in one standalone main-database transaction, so a CAS
        failure records nothing and a crash before the in-memory apply is
        recovered by log replay on the next materialization. The caller must
        not be inside an outer main-database transaction.
        """
        if getattr(self._db, "transaction_depth", 0) > 0 or getattr(
            self._db, "in_transaction", False
        ):
            raise SumStorageError(
                "PlexiSum operation writes require a standalone transaction"
            )
        self._db.begin_transaction()
        try:
            cursor = self._db.execute(
                "UPDATE sum_workbooks SET current_revision = ?, updated_at = ? "
                "WHERE id = ? AND current_revision = ?",
                (
                    int(revision),
                    int(now),
                    int(workbook_id),
                    int(base_revision),
                ),
            )
            try:
                affected = int(cursor.rowcount)
            finally:
                cursor.close()
            if affected != 1:
                raise SumConflictError(conflict_message)
            self._db.execute(
                "INSERT INTO sum_operations (id, workbook_id, operation_id, "
                "actor_id, base_revision, result_revision, operation_type, "
                "sheet_id, payload_json, created_at) VALUES (?, ?, ?, ?, ?, "
                "?, ?, ?, ?, ?)",
                (
                    self._generate_id(),
                    int(workbook_id),
                    str(operation_id),
                    int(user_id),
                    int(base_revision),
                    int(revision),
                    str(operation_type),
                    str(sheet_id),
                    self._json({"sheet_id": str(sheet_id), "cells": cells}),
                    int(now),
                ),
            )
            self._db.commit()
        except Exception:
            if getattr(self._db, "in_transaction", False):
                self._db.rollback()
            raise

    def _delete_blob_confirmed(self, path: str) -> bool:
        """Delete a blob and confirm that it is no longer present.

        Storage adapters return ``False`` both for an already-missing object
        and, in the media boundary, for a swallowed provider error. The
        explicit existence check distinguishes those cases sufficiently for
        retryable cleanup: an absent object is success, while an object that
        remains is failure and its metadata pointer must be retained.
        """
        if not path:
            return True
        try:
            self._blob.delete(path)
        except ValueError:
            # Path-confinement rejection: the path is not addressable in
            # this store (e.g. a legacy non-namespaced blob_path), so
            # there is nothing of ours to delete. Confirm absence via the
            # fail-closed existence probe; absent means done.
            try:
                return not self._blob.exists(path)
            except Exception:
                return False
        except Exception:
            return False
        try:
            # Always verify, even when delete() reported success; some remote
            # adapters acknowledge a request before the object is gone.
            return not self._blob.exists(path)
        except Exception:
            return False

    def _prune_revision_blobs(self, workbook_id: int) -> None:
        """Best-effort GC of unreferenced old immutable revision blobs."""
        try:
            rows = self._db.fetch_all(
                "SELECT id, blob_path FROM sum_revision_blobs "
                "WHERE workbook_id = ? ORDER BY revision DESC, created_at DESC",
                (int(workbook_id),),
            )
        except Exception:
            import utils.logger as logger

            logger.error(
                f"PlexiSum: failed to read workbook {workbook_id} revision metadata",
                exc_info=True,
            )
            return
        # Keep the current blob plus REVISION_KEEP_COUNT of history: the
        # newest row in the table is the live workbook file and must never be
        # deleted, so the GC window starts after it.
        for row in rows[REVISION_KEEP_COUNT + 1 :]:
            path = str(row.get("blob_path") or "")
            try:
                if path:
                    if not self._delete_blob_confirmed(path):
                        raise SumStorageError(
                            f"Revision blob could not be deleted: {path}"
                        )
                self._db.execute(
                    "DELETE FROM sum_revision_blobs WHERE id = ?", (row["id"],)
                )
            except Exception:
                import utils.logger as logger

                logger.debug(
                    "PlexiSum: failed to delete superseded revision blob %s",
                    path,
                    exc_info=True,
                )

    def _save_file(
        self, workbook_id: int, revision: int, raw_bytes: bytes
    ) -> Tuple[str, int, str]:
        """Store a uniquely named immutable revision blob.

        *raw_bytes* must be raw (uncompressed) SQLite bytes.  They are
        wrapped in the PlexiSum container format before storage.  The
        returned checksum is the SHA-256 of the *uncompressed* payload.
        """
        from .container import encode_container

        container = encode_container(raw_bytes)
        key = f"{int(workbook_id)}/rev-{int(revision)}-{uuid.uuid4().hex}.plexisum"
        path = self._blob.store(container, key)
        return path, len(container), hashlib.sha256(raw_bytes).hexdigest()

    def _publish_revision(
        self,
        workbook_id: int,
        expected_revision: int,
        revision: int,
        data: bytes,
        now: int,
        conflict_message: str,
    ) -> Tuple[str, int, str]:
        """Publish one immutable file and atomically advance main metadata."""
        # A caller-owned outer transaction could roll back the metadata after
        # the blob has been stored, leaving an unreachable external object.
        # Revision publication therefore owns its main-DB transaction and must
        # not be nested inside another one.
        if getattr(self._db, "transaction_depth", 0) > 0 or getattr(
            self._db, "in_transaction", False
        ):
            raise SumStorageError(
                "PlexiSum revision writes require a standalone transaction"
            )
        path, size, checksum = self._save_file(workbook_id, revision, data)
        try:
            self._db.begin_transaction()
            cursor = self._db.execute(
                "UPDATE sum_workbooks SET current_revision = ?, blob_revision = ?, "
                "blob_path = ?, file_size = ?, checksum = ?, updated_at = ? "
                "WHERE id = ? AND current_revision = ?",
                (
                    int(revision),
                    int(revision),
                    path,
                    size,
                    checksum,
                    now,
                    int(workbook_id),
                    int(expected_revision),
                ),
            )
            try:
                affected = int(cursor.rowcount)
            finally:
                cursor.close()
            if affected != 1:
                raise SumConflictError(conflict_message)
            self._db.execute(
                "INSERT INTO sum_revision_blobs "
                "(id, workbook_id, revision, blob_path, checksum, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    self._generate_id(),
                    int(workbook_id),
                    int(revision),
                    path,
                    checksum,
                    now,
                ),
            )
            # A full-file rewrite supersedes every buffered operation: the new
            # blob is complete at ``revision``, so the durable log can be
            # compacted up to it in the same transaction.
            self._db.execute(
                "DELETE FROM sum_operations WHERE workbook_id = ? "
                "AND result_revision <= ?",
                (int(workbook_id), int(revision)),
            )
            self._db.commit()
        except Exception:
            if getattr(self._db, "in_transaction", False):
                self._db.rollback()
            self._blob.delete(path)
            raise
        self._cache_current_file(workbook_id, revision, data)
        # The new blob is authoritative; drop any live session so the next
        # access rebuilds from it (its content may lack this rewrite).
        with self._session_lock(int(workbook_id)):
            self._drop_session(int(workbook_id))
        self._prune_revision_blobs(workbook_id)
        return path, size, checksum

    @staticmethod
    def _open_sqlite(data: bytes) -> Tuple[sqlite3.Connection, str]:
        """Open workbook bytes as a sqlite connection over a temp file.

        Feature tables (validations, conditional formats, comments, freeze)
        are backfilled idempotently on every open so older ``.plexisum``
        files created before those tables shipped work transparently.
        """
        if data[:4] == b"PXSM":
            from .container import decode_container

            data, _, _ = decode_container(data)
        handle = tempfile.NamedTemporaryFile(
            prefix="plexisum-", suffix=".plexisum", delete=False
        )
        handle.write(data)
        handle.close()
        connection = sqlite3.connect(handle.name)
        connection.row_factory = sqlite3.Row
        try:
            ensure_feature_tables(connection)
            format_row = connection.execute(
                "SELECT value FROM sum_meta WHERE key = 'format_version'"
            ).fetchone()
            if format_row is None or format_row["value"] != PLEXISUM_FORMAT_VERSION:
                raise SumStorageError("Unsupported PlexiSum workbook format")
        except (sqlite3.Error, SumStorageError) as exc:
            SumManagerRevisions._close_sqlite(connection, handle.name)
            if isinstance(exc, SumStorageError):
                raise
            raise SumStorageError("Workbook file schema could not be upgraded") from exc
        return connection, handle.name

    @staticmethod
    def _close_sqlite(connection: sqlite3.Connection, path: str) -> None:
        try:
            connection.close()
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

    @staticmethod
    def _open_sqlite_memory(data: bytes) -> Tuple[sqlite3.Connection, Optional[str]]:
        """Open workbook bytes as an in-memory sqlite connection.

        Hot read paths (viewport, sheet listings) used to write every blob to
        a temp file and unlink it on every call. ``Connection.deserialize``
        (Python >= 3.11) loads the bytes straight into ``:memory:``, avoiding
        the temp-file I/O entirely. Runtimes without ``deserialize`` fall
        back to the file-backed opener; callers branch on the returned path.
        """
        if data[:4] == b"PXSM":
            from .container import decode_container

            data, _, _ = decode_container(data)
        connection = None
        try:
            if getattr(sqlite3.Connection, "deserialize", None) is None:
                raise RuntimeError("deserialize unavailable")
            connection = sqlite3.connect(":memory:")
            connection.deserialize(data)
        except Exception:
            # Close the :memory: connection that may have been created
            # before falling back to a temp-file-backed connection.
            if connection is not None:
                try:
                    connection.close()
                except sqlite3.Error:
                    pass
            return SumManagerRevisions._open_sqlite(data)
        connection.row_factory = sqlite3.Row
        try:
            ensure_feature_tables(connection)
            format_row = connection.execute(
                "SELECT value FROM sum_meta WHERE key = 'format_version'"
            ).fetchone()
            if format_row is None or format_row["value"] != PLEXISUM_FORMAT_VERSION:
                raise SumStorageError("Unsupported PlexiSum workbook format")
        except (sqlite3.Error, SumStorageError) as exc:
            try:
                connection.close()
            except sqlite3.Error:
                pass
            if isinstance(exc, SumStorageError):
                raise
            raise SumStorageError("Workbook file schema could not be upgraded") from exc
        return connection, None

    @staticmethod
    def _close_sqlite_memory(connection: sqlite3.Connection) -> None:
        try:
            connection.close()
        except sqlite3.Error:
            pass

    def _read_sheets_from_file(self, data: bytes) -> List[Dict[str, Any]]:
        connection, path = self._open_sqlite_memory(data)
        try:
            rows = connection.execute(
                "SELECT id, name, position, created_at, updated_at FROM sum_sheets ORDER BY position, id"
            ).fetchall()
            return [
                {
                    "id": str(row["id"]),
                    "name": row["name"],
                    "position": int(row["position"]),
                    "created_at": int(row["created_at"]),
                    "updated_at": int(row["updated_at"]),
                }
                for row in rows
            ]
        except sqlite3.Error as exc:
            raise SumStorageError("Workbook file schema is invalid") from exc
        finally:
            if path is None:
                self._close_sqlite_memory(connection)
            else:
                self._close_sqlite(connection, path)

    def _ensure_sheet(self, data: bytes, sheet_id: str) -> None:
        connection, path = self._open_sqlite(data)
        try:
            row = connection.execute(
                "SELECT id FROM sum_sheets WHERE id = ? AND deleted = 0",
                (str(sheet_id),),
            ).fetchone()
        finally:
            self._close_sqlite(connection, path)
        if row is None:
            raise SumNotFoundError("Sheet not found")

    def _resolver_for(
        self, connection: sqlite3.Connection
    ) -> Callable[[Optional[str], int, int], Optional[Dict[str, Any]]]:
        """Build a formula resolver backed by the open workbook connection.

        Formula references use sheet *names* (``Sheet2!A1``); the resolver
        maps names to the sheet ids stored in the file, memoizes lookups so
        repeated references do not re-query, and returns ``None`` for empty
        cells (evaluated as blank in formulas).
        """
        sheet_map: Dict[str, str] = {}
        try:
            for row in connection.execute(
                "SELECT id, name FROM sum_sheets WHERE deleted = 0"
            ).fetchall():
                sheet_map[str(row["name"])] = str(row["id"])
        except sqlite3.Error as exc:
            import utils.logger as logger

            logger.error(f"PlexiSum formula resolver could not load sheet names: {exc}")
        cache: Dict[Tuple[str, int, int], Optional[Dict[str, Any]]] = {}

        def resolver(
            sheet: Optional[str], row: int, column: int
        ) -> Optional[Dict[str, Any]]:
            sheet_id = sheet_map.get(str(sheet), str(sheet)) if sheet else ""
            key = (sheet_id, int(row), int(column))
            if key in cache:
                return cache[key]
            row_data = connection.execute(
                "SELECT value_type, value_json, formula FROM sum_cells "
                "WHERE sheet_id = ? AND row_index = ? AND column_index = ?",
                (sheet_id, int(row), int(column)),
            ).fetchone()
            if row_data is None:
                cache[key] = None
                return None
            result: Dict[str, Any] = {
                "value_type": row_data["value_type"],
                "value": self._decode(row_data["value_json"]),
                "formula": row_data["formula"],
            }
            cache[key] = result
            return result

        return resolver

    # ── workbook CRUD ────────────────────────────────────────────────────────
