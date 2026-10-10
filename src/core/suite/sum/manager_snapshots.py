"""Sum manager mixin: snapshots and sheet CRUD (split from manager.py)."""

import hashlib
import sqlite3
import time

from typing import Any, Dict, List, Optional

from .manager_protocol import SumManagerProtocol
from ._shared import (
    MAX_SNAPSHOTS_KEPT,
    SumNotFoundError,
    SumStorageError,
    SumValidationError,
)


class SumManagerSnapshots(SumManagerProtocol):
    def create_snapshot(self, user_id: int, workbook_id: int) -> Dict[str, Any]:
        workbook = self._workbook(user_id, workbook_id, mutation=True)
        if getattr(self._db, "transaction_depth", 0) > 0 or getattr(
            self._db, "in_transaction", False
        ):
            raise SumStorageError(
                "PlexiSum snapshot writes require a standalone transaction"
            )
        data = self._file_bytes(workbook_id)
        snapshot_id = self._generate_id()
        key = f"{int(workbook_id)}/snapshot-{snapshot_id}.plexisum"
        checksum = hashlib.sha256(data).hexdigest()
        now = int(time.time() * 1000)
        connection = None
        tmp_path = None
        try:
            connection, tmp_path = self._open_sqlite(data)
            cell_count = int(
                connection.execute("SELECT COUNT(*) AS c FROM sum_cells").fetchone()[
                    "c"
                ]
            )
        except (sqlite3.Error, SumStorageError) as exc:
            raise SumStorageError("Snapshot source workbook could not be read") from exc
        finally:
            if connection is not None and tmp_path is not None:
                self._close_sqlite(connection, tmp_path)

        # Validate the source before creating the external snapshot object. This
        # keeps schema/format failures from producing an unreachable blob.
        from .container import encode_container

        container_data = encode_container(data)
        path = self._blob.store(container_data, key)
        try:
            self._db.begin_transaction()
            self._db.execute(
                "INSERT INTO sum_snapshots (id, workbook_id, created_by, revision, blob_path, checksum, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    snapshot_id,
                    workbook_id,
                    user_id,
                    int(workbook["current_revision"]),
                    path,
                    checksum,
                    now,
                ),
            )
            self._db.commit()
        except Exception:
            if getattr(self._db, "in_transaction", False):
                self._db.rollback()
            if not self._delete_blob_confirmed(path):
                # Preserve a durable retry pointer when storage refuses the
                # compensating delete. A later GC/account purge can retry it.
                try:
                    self._db.begin_transaction()
                    self._db.insert_or_ignore(
                        "sum_snapshots",
                        [
                            "id",
                            "workbook_id",
                            "created_by",
                            "revision",
                            "blob_path",
                            "checksum",
                            "created_at",
                        ],
                        (
                            snapshot_id,
                            workbook_id,
                            user_id,
                            int(workbook["current_revision"]),
                            path,
                            checksum,
                            now,
                        ),
                    )
                    self._db.commit()
                except Exception:
                    if getattr(self._db, "in_transaction", False):
                        self._db.rollback()
            raise
        self._gc_snapshots(int(workbook_id))
        self._audit(
            "snapshot",
            "workbook",
            workbook_id,
            user_id,
            {
                "snapshot_id": str(snapshot_id),
                "revision": int(workbook["current_revision"]),
            },
        )
        return {
            "id": str(snapshot_id),
            "workbook_id": str(workbook_id),
            "revision": int(workbook["current_revision"]),
            "checksum": checksum,
            "cell_count": cell_count,
            "created_at": now,
        }

    def _gc_snapshots(self, workbook_id: int) -> None:
        """Retain only the newest ``MAX_SNAPSHOTS_KEPT`` snapshots per workbook.

        Snapshot blobs are immutable copies of the workbook file and would
        otherwise accumulate without bound. Best-effort and idempotent.
        """
        try:
            count_row = self._db.fetch_one(
                "SELECT COUNT(*) AS c FROM sum_snapshots WHERE workbook_id = ?",
                (int(workbook_id),),
            )
            count = int(count_row["c"]) if count_row else 0
            if count <= MAX_SNAPSHOTS_KEPT:
                return
            stale = self._db.fetch_all(
                "SELECT id, blob_path FROM sum_snapshots WHERE workbook_id = ? "
                "ORDER BY created_at ASC LIMIT ?",
                (int(workbook_id), count - MAX_SNAPSHOTS_KEPT),
            )
            for snap in stale:
                snap_path = str(snap.get("blob_path") or "")
                if snap_path and not self._delete_blob_confirmed(snap_path):
                    # Keep the row as a retry pointer. Removing it here would
                    # orphan a snapshot blob after a transient storage failure.
                    continue
                self._db.execute(
                    "DELETE FROM sum_snapshots WHERE id = ?", (int(snap["id"]),)
                )
        except Exception:
            import utils.logger as logger

            logger.warning("PlexiSum snapshot GC failed", exc_info=True)

    def list_snapshots(
        self, user_id: int, workbook_id: int, limit: int = 50
    ) -> List[Dict[str, Any]]:
        limit = max(1, min(int(limit), 100))
        self._workbook(user_id, workbook_id)
        rows = self._db.fetch_all(
            "SELECT id, created_by, revision, checksum, created_at FROM sum_snapshots WHERE workbook_id = ? ORDER BY created_at DESC LIMIT ?",
            (int(workbook_id), limit),
        )
        return [
            {
                "id": str(row["id"]),
                "workbook_id": str(workbook_id),
                "created_by": str(row["created_by"]),
                "revision": int(row["revision"]),
                "checksum": row.get("checksum"),
                "created_at": int(row["created_at"]),
            }
            for row in rows
        ]

    def restore_snapshot(
        self, user_id: int, workbook_id: int, snapshot_id: int
    ) -> Dict[str, Any]:
        self._workbook(user_id, workbook_id, mutation=True)
        row = self._db.fetch_one(
            "SELECT revision, blob_path, checksum FROM sum_snapshots WHERE id = ? AND workbook_id = ?",
            (int(snapshot_id), int(workbook_id)),
        )
        if not row or not row.get("blob_path"):
            raise SumNotFoundError("Snapshot not found")
        try:
            container_data = self._blob.retrieve(str(row["blob_path"]))
        except Exception as exc:
            raise SumStorageError("Snapshot file could not be read") from exc
        if container_data[:4] == b"PXSM":
            from .container import decode_container

            db_bytes, _, _ = decode_container(container_data)
        else:
            db_bytes = container_data
        if row.get("checksum"):
            actual = hashlib.sha256(db_bytes).hexdigest()
            if actual != row["checksum"]:
                raise SumValidationError("Snapshot checksum mismatch")
        current = self._db.fetch_one(
            "SELECT current_revision FROM sum_workbooks WHERE id = ?",
            (int(workbook_id),),
        )
        base_revision = int(current["current_revision"]) if current else 0
        revision = base_revision + 1
        now = int(time.time() * 1000)
        path, size, checksum = self._publish_revision(
            int(workbook_id),
            int(base_revision),
            int(revision),
            db_bytes,
            now,
            "Workbook revision changed during restore",
        )
        self._audit(
            "restore_snapshot",
            "workbook",
            workbook_id,
            user_id,
            {"snapshot_id": str(snapshot_id), "revision": revision},
        )
        try:
            row = self._db.fetch_one(
                "SELECT * FROM sum_workbooks WHERE id = ?", (int(workbook_id),)
            )
            if row:
                self._emit_update(row, revision, user_id)
        except Exception:
            import utils.logger as logger

            logger.debug("PlexiSum update emit failed", exc_info=True)
        return self.get_workbook(user_id, workbook_id)

    # ── sheet management ─────────────────────────────────────────────────────

    def create_sheet(
        self, user_id: int, workbook_id: int, name: str, position: Optional[int] = None
    ) -> Dict[str, Any]:
        self._workbook(user_id, workbook_id, mutation=True)
        name = str(name or "").strip()
        if not name or len(name) > 200:
            raise SumValidationError("sheet name must be between 1 and 200 characters")
        data = self._file_bytes(workbook_id)
        now = int(time.time() * 1000)
        sheet_id = self._generate_id()
        connection, path = self._open_sqlite(data)
        try:
            duplicate = connection.execute(
                "SELECT id FROM sum_sheets WHERE name = ? AND deleted = 0",
                (name,),
            ).fetchone()
            if duplicate:
                raise SumValidationError("A sheet with this name already exists")
            if position is None:
                max_row = connection.execute(
                    "SELECT MAX(position) AS max_position FROM sum_sheets WHERE deleted = 0"
                ).fetchone()
                position = (
                    int(max_row["max_position"]) + 1
                    if max_row and max_row["max_position"] is not None
                    else 0
                )
            connection.execute(
                "INSERT INTO sum_sheets (id, name, position, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (str(sheet_id), name, int(position), now, now),
            )
            connection.commit()
            with open(path, "rb") as file_handle:
                updated = file_handle.read()
        except sqlite3.Error as exc:
            raise SumStorageError("Workbook file could not be updated") from exc
        finally:
            self._close_sqlite(connection, path)
        self._write_file_revision(workbook_id, user_id, "create_sheet", updated)
        return {
            "id": str(sheet_id),
            "name": name,
            "position": int(position),
            "created_at": now,
            "updated_at": now,
        }

    def rename_sheet(
        self, user_id: int, workbook_id: int, sheet_id: int, name: str
    ) -> Dict[str, Any]:
        self._workbook(user_id, workbook_id, mutation=True)
        name = str(name or "").strip()
        if not name or len(name) > 200:
            raise SumValidationError("sheet name must be between 1 and 200 characters")
        data = self._file_bytes(workbook_id)
        now = int(time.time() * 1000)
        connection, path = self._open_sqlite(data)
        try:
            sheet = connection.execute(
                "SELECT id FROM sum_sheets WHERE id = ? AND deleted = 0",
                (str(sheet_id),),
            ).fetchone()
            if sheet is None:
                raise SumNotFoundError("Sheet not found")
            duplicate = connection.execute(
                "SELECT id FROM sum_sheets WHERE name = ? AND id != ? AND deleted = 0",
                (name, str(sheet_id)),
            ).fetchone()
            if duplicate:
                raise SumValidationError("A sheet with this name already exists")
            connection.execute(
                "UPDATE sum_sheets SET name = ?, updated_at = ? WHERE id = ?",
                (name, now, str(sheet_id)),
            )
            connection.commit()
            with open(path, "rb") as file_handle:
                updated = file_handle.read()
        except sqlite3.Error as exc:
            raise SumStorageError("Workbook file could not be updated") from exc
        finally:
            self._close_sqlite(connection, path)
        self._write_file_revision(workbook_id, user_id, "rename_sheet", updated)
        return {"id": str(sheet_id), "name": name}

    def delete_sheet(self, user_id: int, workbook_id: int, sheet_id: int) -> None:
        self._workbook(user_id, workbook_id, mutation=True)
        data = self._file_bytes(workbook_id)
        now = int(time.time() * 1000)
        connection, path = self._open_sqlite(data)
        try:
            sheet = connection.execute(
                "SELECT id FROM sum_sheets WHERE id = ? AND deleted = 0",
                (str(sheet_id),),
            ).fetchone()
            if sheet is None:
                raise SumNotFoundError("Sheet not found")
            remaining = connection.execute(
                "SELECT COUNT(*) AS count FROM sum_sheets WHERE deleted = 0 AND id != ?",
                (str(sheet_id),),
            ).fetchone()
            if not remaining or int(remaining["count"]) == 0:
                raise SumValidationError("A workbook must keep at least one sheet")
            connection.execute(
                "UPDATE sum_sheets SET deleted = 1, updated_at = ? WHERE id = ?",
                (now, str(sheet_id)),
            )
            connection.commit()
            with open(path, "rb") as file_handle:
                updated = file_handle.read()
        except sqlite3.Error as exc:
            raise SumStorageError("Workbook file could not be updated") from exc
        finally:
            self._close_sqlite(connection, path)
        self._write_file_revision(workbook_id, user_id, "delete_sheet", updated)

    def _write_file_revision(
        self, workbook_id: int, user_id: int, action: str, updated: bytes
    ) -> int:
        current = self._db.fetch_one(
            "SELECT current_revision FROM sum_workbooks WHERE id = ?",
            (int(workbook_id),),
        )
        base = int(current["current_revision"]) if current else 0
        revision = base + 1
        now = int(time.time() * 1000)
        path, size, checksum = self._publish_revision(
            int(workbook_id),
            int(base),
            int(revision),
            updated,
            now,
            "Workbook revision changed during update",
        )
        self._audit(
            action,
            "workbook",
            workbook_id,
            user_id,
            {"revision": revision},
        )
        try:
            row = self._db.fetch_one(
                "SELECT * FROM sum_workbooks WHERE id = ?", (int(workbook_id),)
            )
            if row:
                self._emit_update(row, revision, user_id)
        except Exception:
            import utils.logger as logger

            logger.debug("PlexiSum update emit failed", exc_info=True)
        return revision

    # ── sorting ──────────────────────────────────────────────────────────────
