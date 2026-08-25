"""Sum manager mixin: workbook-file cache and coalesced write sessions (split from manager.py)."""

import hashlib
import os
import sqlite3
import tempfile
import threading
import time

from typing import Any, Dict

from .schema import (
    PLEXISUM_FORMAT_VERSION,
    ensure_feature_tables,
)
from . import _shared as _shared_state
from .manager_protocol import SumManagerProtocol
from ._shared import (
    SUM_SESSION_MAX_ENTRIES,
    SumConflictError,
    SumStorageError,
    _LiveSession,
    _SESSION_GUARD,
    _SESSION_TMP_PATHS,
    _WORKBOOK_FILE_CACHE,
    _WORKBOOK_SESSION_LOCKS,
    _WORKBOOK_SESSIONS,
)


class SumManagerSessions(SumManagerProtocol):
    def _file_bytes(self, workbook_id: int) -> bytes:
        """Return the workbook's current materialized state as bytes.

        The live session already includes unflushed operations, so this
        serializes the in-memory copy instead of downloading a blob. Used by
        read paths that need an immutable byte snapshot (downloads, snapshots,
        full-file rewrites); hot viewport reads query the session directly.
        """
        with self._session_lock(int(workbook_id)):
            connection = self._materialized_connection_unlocked(int(workbook_id))
            return self._serialize_connection(connection)

    def _cache_current_file(self, workbook_id: int, revision: int, data: bytes) -> None:
        """Publish just-committed file bytes to the read cache."""
        _WORKBOOK_FILE_CACHE.put((int(workbook_id), int(revision)), data)

    # ── live sessions (coalesced writes) ────────────────────────────────────

    @staticmethod
    def _session_lock(workbook_id: int) -> threading.RLock:
        """Return the per-workbook session lock, shared across instances.

        Touching the workbook here also marks the session recently used so
        eviction never races an in-flight holder (a lock holder is by
        definition not the least-recently-used candidate).
        """
        with _SESSION_GUARD:
            lock = _WORKBOOK_SESSION_LOCKS.setdefault(
                int(workbook_id), threading.RLock()
            )
        session = _WORKBOOK_SESSIONS.get(int(workbook_id))
        if session is not None:
            session.last_access_ms = int(time.time() * 1000)
        return lock

    @staticmethod
    def _drop_session(workbook_id: int) -> None:
        """Discard a live session; its unflushed ops stay durable in the log."""
        with _SESSION_GUARD:
            session = _WORKBOOK_SESSIONS.pop(int(workbook_id), None)
        if session is not None:
            SumManagerSessions._close_session_connection(session.connection)

    @staticmethod
    def _close_session_connection(connection: "sqlite3.Connection") -> None:
        try:
            connection.close()
        except sqlite3.Error:
            pass
        path = _SESSION_TMP_PATHS.pop(id(connection), None)
        if path:
            try:
                os.unlink(path)
            except OSError:
                pass

    def _materialized_connection(self, workbook_id: int) -> "sqlite3.Connection":
        with self._session_lock(int(workbook_id)):
            return self._materialized_connection_unlocked(int(workbook_id))

    def _materialized_connection_unlocked(
        self, workbook_id: int
    ) -> "sqlite3.Connection":
        """Return a live connection holding the workbook's current state.

        Callers must hold ``self._session_lock(workbook_id)``. The connection
        is created on first use from the stored blob plus the durable
        operation log, and rebuilt whenever another writer promoted a newer
        blob, so it always reflects ``current_revision``.
        """
        row = self._db.fetch_one(
            "SELECT blob_revision, current_revision, blob_path, checksum "
            "FROM sum_workbooks WHERE id = ? AND deleted = 0",
            (int(workbook_id),),
        )
        if not row or not row.get("blob_path"):
            raise SumStorageError("Workbook file is not available")
        blob_revision = int(row.get("blob_revision") or 0)
        current_revision = int(row.get("current_revision") or 0)
        session = _WORKBOOK_SESSIONS.get(int(workbook_id))
        if session is not None:
            session.last_access_ms = int(time.time() * 1000)
            if session.loaded_blob_revision == blob_revision:
                return session.connection
            # Another writer promoted a newer blob; rebuild from it.
            SumManagerSessions._drop_session(int(workbook_id))
        data = self._blob_bytes(int(workbook_id), row)
        connection = self._open_session_connection(data)
        try:
            if current_revision > blob_revision:
                self._replay_unflushed(connection, int(workbook_id), blob_revision)
        except Exception:
            SumManagerSessions._close_session_connection(connection)
            raise
        session = _LiveSession(connection, blob_revision)
        session.pending = max(0, current_revision - blob_revision)
        with _SESSION_GUARD:
            _WORKBOOK_SESSIONS[int(workbook_id)] = session
        self._evict_sessions()
        # Refresh the cached index/calculation status once per materialization
        # (session-cached, so not per read). Never fail the connection over it.
        try:
            session = _WORKBOOK_SESSIONS.get(int(workbook_id))
            if session is None or session.pending > 0:
                self._refresh_workbook_status(connection, int(workbook_id))
        except Exception:
            import utils.logger as logger

            logger.debug("PlexiSum workbook status refresh failed", exc_info=True)
        return connection

    def _refresh_workbook_status(
        self, connection: "sqlite3.Connection", workbook_id: int
    ) -> None:
        """Recompute index/calculation state from the live workbook file.

        ``index_state`` reflects the sum_indexes table (none / indexed /
        building); ``calculation_state`` is 'calculated' when any sheet has
        formula cells (the server evaluates them on read) and 'none'
        otherwise. Persisted to the main metadata row so list responses do not
        pay per-file I/O.
        """
        index_row = connection.execute(
            "SELECT COUNT(*) AS total, "
            "COALESCE(SUM(CASE WHEN state = 'ready' THEN 1 ELSE 0 END), 0) AS ready "
            "FROM sum_indexes"
        ).fetchone()
        total = int(index_row["total"] or 0) if index_row else 0
        ready = int(index_row["ready"] or 0) if index_row else 0
        if total == 0:
            index_state = "none"
        elif ready >= total:
            index_state = "indexed"
        else:
            index_state = "building"
        formula_row = connection.execute(
            "SELECT COUNT(*) AS count FROM sum_cells "
            "WHERE formula IS NOT NULL AND formula != ''"
        ).fetchone()
        formula_count = int(formula_row["count"] or 0) if formula_row else 0
        calculation_state = "calculated" if formula_count > 0 else "none"
        self._db.execute(
            "UPDATE sum_workbooks SET index_state = ?, calculation_state = ? "
            "WHERE id = ?",
            (index_state, calculation_state, int(workbook_id)),
        )

    def _blob_bytes(self, workbook_id: int, row) -> bytes:
        """Fetch and integrity-check the stored blob for a workbook row.

        The blob on disk is a PlexiSum container.  This method decodes it,
        verifies the SHA-256 checksum over the *uncompressed* payload, and
        caches the raw SQLite bytes so subsequent hot reads avoid the
        decompression + I/O cost.
        """
        cached = _WORKBOOK_FILE_CACHE.get(
            (int(workbook_id), int(row.get("blob_revision") or 0))
        )
        if cached is not None:
            if (
                row.get("checksum")
                and hashlib.sha256(cached).hexdigest() != row["checksum"]
            ):
                _WORKBOOK_FILE_CACHE.drop_workbook(int(workbook_id))
                raise SumStorageError("Workbook file checksum mismatch")
            return cached
        try:
            container_data = self._blob.retrieve(str(row["blob_path"]))
        except Exception as exc:
            raise SumStorageError("Workbook file could not be read") from exc
        if container_data[:4] == b"PXSM":
            from .container import decode_container

            db_bytes, _, _ = decode_container(container_data)
        else:
            db_bytes = container_data
        if (
            row.get("checksum")
            and hashlib.sha256(db_bytes).hexdigest() != row["checksum"]
        ):
            raise SumStorageError("Workbook file checksum mismatch")
        _WORKBOOK_FILE_CACHE.put(
            (int(workbook_id), int(row.get("blob_revision") or 0)), db_bytes
        )
        return db_bytes

    @staticmethod
    def _open_session_connection(data: bytes) -> "sqlite3.Connection":
        """Open workbook bytes as an in-memory connection usable across threads.

        Unlike the per-read openers this connection is long-lived, so it is
        created with ``check_same_thread=False`` and guarded by the
        per-workbook session lock. Runtimes without ``deserialize`` fall back
        to a private temp file whose path is registered for cleanup.
        """
        if data[:4] == b"PXSM":
            from .container import decode_container

            data, _, _ = decode_container(data)
        connection = None
        try:
            if getattr(sqlite3.Connection, "deserialize", None) is None:
                raise RuntimeError("deserialize unavailable")
            connection = sqlite3.connect(":memory:", check_same_thread=False)
            connection.deserialize(data)
        except Exception:
            # Close the :memory: connection that may have been created
            # before falling back to a temp-file-backed connection.
            if connection is not None:
                try:
                    connection.close()
                except sqlite3.Error:
                    pass
            handle = tempfile.NamedTemporaryFile(
                prefix="plexisum-", suffix=".plexisum", delete=False
            )
            handle.write(data)
            handle.close()
            connection = sqlite3.connect(handle.name, check_same_thread=False)
            _SESSION_TMP_PATHS[id(connection)] = handle.name
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
        return connection

    @staticmethod
    def _serialize_connection(connection: "sqlite3.Connection") -> bytes:
        """Serialize an in-memory session to a PlexiSum container.

        The returned bytes are a complete container (header + compressed body +
        checksum) ready for storage.
        """
        from .container import encode_container

        try:
            if getattr(sqlite3.Connection, "serialize", None) is None:
                raise RuntimeError("serialize unavailable")
            raw = connection.serialize()
        except Exception:
            # Fallback for runtimes without serialize or WAL-mode sources.
            handle = tempfile.NamedTemporaryFile(
                prefix="plexisum-", suffix=".plexisum", delete=False
            )
            handle.close()
            try:
                connection.execute("VACUUM INTO ?", (handle.name,))
                with open(handle.name, "rb") as file_handle:
                    raw = file_handle.read()
            finally:
                try:
                    os.unlink(handle.name)
                except OSError:
                    pass
        return encode_container(raw)

    def _replay_unflushed(
        self,
        connection: "sqlite3.Connection",
        workbook_id: int,
        after_revision: int,
    ) -> None:
        """Apply durable log rows the stored blob does not yet contain."""
        rows = self._db.fetch_all(
            "SELECT operation_id, actor_id, base_revision, result_revision, "
            "operation_type, sheet_id, payload_json, created_at "
            "FROM sum_operations WHERE workbook_id = ? AND result_revision > ? "
            "ORDER BY result_revision ASC",
            (int(workbook_id), int(after_revision)),
        )
        for row in rows:
            payload = self._decode(row["payload_json"], {})
            cells = payload.get("cells", [])
            revision = int(row["result_revision"])
            sheet_id = row.get("sheet_id") or payload.get("sheet_id")
            for cell in cells:
                try:
                    normalized = {
                        "row": int(cell["row"]),
                        "column": int(cell["column"]),
                        "value_type": str(cell.get("value_type", "blank")),
                        "value": cell.get("value"),
                        "formula": cell.get("formula"),
                        "style": cell.get("style"),
                    }
                except (KeyError, TypeError, ValueError):
                    continue
                self._apply_normalized_cell(
                    connection,
                    str(sheet_id),
                    normalized,
                    revision,
                    int(row["created_at"]),
                )
            connection.execute(
                "INSERT INTO sum_operations (operation_id, actor_id, "
                "base_revision, result_revision, operation_type, payload_json, "
                "created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    row["operation_id"],
                    row["actor_id"],
                    int(row["base_revision"]),
                    revision,
                    row["operation_type"],
                    self._json({"sheet_id": str(sheet_id), "cells": cells}),
                    int(row["created_at"]),
                ),
            )
        if rows:
            connection.commit()

    def _apply_normalized_cell(
        self,
        connection: "sqlite3.Connection",
        sheet_id: str,
        cell: Dict[str, Any],
        revision: int,
        now: int,
    ) -> None:
        """Upsert one normalized cell, honoring the __preserve__ sentinel."""
        row_index = cell["row"]
        column_index = cell["column"]
        if cell["value_type"] == "__preserve__":
            previous = connection.execute(
                "SELECT value_type, value_json, formula FROM sum_cells "
                "WHERE sheet_id = ? AND row_index = ? AND column_index = ?",
                (sheet_id, row_index, column_index),
            ).fetchone()
            if previous:
                value_type = previous["value_type"]
                value_json = previous["value_json"]
                formula = previous["formula"]
            else:
                value_type = "blank"
                value_json = self._json(None)
                formula = None
            connection.execute(
                "INSERT INTO sum_cells (sheet_id, row_index, column_index, "
                "value_type, value_json, formula, style_json, revision, "
                "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(sheet_id, row_index, column_index) DO UPDATE SET "
                "value_type = excluded.value_type, value_json = excluded.value_json, "
                "formula = excluded.formula, style_json = excluded.style_json, "
                "revision = excluded.revision, updated_at = excluded.updated_at",
                (
                    sheet_id,
                    row_index,
                    column_index,
                    value_type,
                    value_json,
                    formula,
                    self._json(cell["style"]),
                    revision,
                    now,
                ),
            )
            # Invalidate any segment covering this row
            self._invalidate_segment(connection, sheet_id, column_index, row_index)
            return
        connection.execute(
            "INSERT INTO sum_cells (sheet_id, row_index, column_index, "
            "value_type, value_json, formula, style_json, revision, "
            "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(sheet_id, row_index, column_index) DO UPDATE SET "
            "value_type = excluded.value_type, value_json = excluded.value_json, "
            "formula = excluded.formula, style_json = excluded.style_json, "
            "revision = excluded.revision, updated_at = excluded.updated_at",
            (
                sheet_id,
                row_index,
                column_index,
                cell["value_type"],
                self._json(cell["value"]),
                cell["formula"],
                self._json(cell["style"]),
                revision,
                now,
            ),
        )
        # Invalidate any segment covering this row
        self._invalidate_segment(connection, sheet_id, column_index, row_index)

    def _invalidate_segment(
        self,
        connection: "sqlite3.Connection",
        sheet_id: str,
        column_index: int,
        row_index: int,
    ) -> None:
        """Mark a column segment as stale so it gets rebuilt on next read."""
        try:
            connection.execute(
                "DELETE FROM sum_column_segments "
                "WHERE sheet_id = ? AND column_id = ? AND row_start <= ? AND row_end > ?",
                (sheet_id, str(column_index), row_index, row_index),
            )
        except sqlite3.Error:
            pass

    def _maybe_flush(self, workbook_id: int) -> None:
        """Promote the live copy when the coalescing window is exceeded.

        Called under the per-workbook session lock after each acknowledged
        operation. A flush failure never fails the operation itself (it is
        already durable); the session is dropped so the next access rebuilds
        from the log and retries the flush on a later write.
        """
        now = int(time.time() * 1000)
        session = _WORKBOOK_SESSIONS.get(int(workbook_id))
        if session is None:
            return
        if session.pending < _shared_state.SUM_FLUSH_MAX_OPS and (
            now - session.last_flush_ms < _shared_state.SUM_FLUSH_MAX_MS
        ):
            return
        try:
            self._flush_session_unlocked(int(workbook_id), session)
        except Exception:
            import utils.logger as logger

            logger.debug(
                "PlexiSum: coalesced flush failed for workbook %s",
                workbook_id,
                exc_info=True,
            )
            SumManagerSessions._drop_session(int(workbook_id))

    def _flush_session_unlocked(self, workbook_id: int, session: _LiveSession) -> None:
        """Store the live copy as a new immutable blob, CAS-gated on blob_revision.

        The CAS makes concurrent multi-worker flushes safe: exactly one writer
        promotes its snapshot; losers delete their blob and rebuild from the
        winner (their operations remain durable in the log). The log is
        compacted up to the flushed revision in the same transaction.
        """
        if getattr(self._db, "transaction_depth", 0) > 0 or getattr(
            self._db, "in_transaction", False
        ):
            raise SumStorageError(
                "PlexiSum revision writes require a standalone transaction"
            )
        now = int(time.time() * 1000)
        revision = self._current_revision(int(workbook_id))
        data = self._serialize_connection(session.connection)
        path, size, checksum = self._save_file(int(workbook_id), int(revision), data)
        try:
            self._db.begin_transaction()
            cursor = self._db.execute(
                "UPDATE sum_workbooks SET blob_revision = ?, blob_path = ?, "
                "file_size = ?, checksum = ?, updated_at = ? "
                "WHERE id = ? AND blob_revision = ?",
                (
                    int(revision),
                    path,
                    size,
                    checksum,
                    now,
                    int(workbook_id),
                    session.loaded_blob_revision,
                ),
            )
            try:
                affected = int(cursor.rowcount)
            finally:
                cursor.close()
            if affected != 1:
                raise SumConflictError("Workbook was flushed by another writer")
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
            self._db.execute(
                "DELETE FROM sum_operations WHERE workbook_id = ? "
                "AND result_revision <= ?",
                (int(workbook_id), int(revision)),
            )
            self._db.commit()
        except Exception:
            if getattr(self._db, "in_transaction", False):
                self._db.rollback()
            try:
                self._blob.delete(path)
            except Exception:
                pass
            raise
        self._cache_current_file(int(workbook_id), int(revision), data)
        session.loaded_blob_revision = int(revision)
        session.pending = 0
        session.last_flush_ms = now
        self._prune_revision_blobs(int(workbook_id))

    def _evict_sessions(self) -> None:
        """Drop the least recently used sessions beyond the entry cap.

        Only sessions whose lock can be acquired without blocking are
        evicted, so a session mid-operation is never closed underneath its
        holder; those are swept on a later eviction pass. Sessions with
        pending writes are flushed first (best-effort).
        """
        while len(_WORKBOOK_SESSIONS) > SUM_SESSION_MAX_ENTRIES:
            with _SESSION_GUARD:
                candidates = sorted(
                    _WORKBOOK_SESSIONS,
                    key=lambda wb: _WORKBOOK_SESSIONS[wb].last_access_ms,
                )
            victim = None
            victim_lock = None
            for candidate in candidates:
                lock = _WORKBOOK_SESSION_LOCKS.get(candidate)
                if lock is not None and not lock.acquire(blocking=False):
                    continue
                victim = candidate
                victim_lock = lock
                break
            if victim is None:
                # Every candidate is in use; stop rather than grow unbounded.
                return
            try:
                session = _WORKBOOK_SESSIONS.pop(victim, None)
                if session is None:
                    continue
                if session.pending > 0:
                    try:
                        self._flush_session_unlocked(victim, session)
                    except Exception:
                        import utils.logger as logger

                        logger.debug(
                            "PlexiSum: eviction flush failed for workbook %s",
                            victim,
                            exc_info=True,
                        )
                SumManagerSessions._close_session_connection(session.connection)
            finally:
                if victim_lock is not None:
                    victim_lock.release()

    def _current_revision(self, workbook_id: int) -> int:
        row = self._db.fetch_one(
            "SELECT current_revision FROM sum_workbooks WHERE id = ?",
            (int(workbook_id),),
        )
        return int(row["current_revision"]) if row else 0
