"""Sum manager mixin: operation application and structural edits (split from manager.py)."""

import json
import sqlite3
import time

from typing import Any, Dict, List

from .schema import (
    SUM_OPERATION_TYPES,
    SUM_VALUE_TYPES,
)
from .segments import SegmentEncoder
from .manager_protocol import SumManagerProtocol
from ._shared import (
    INDEX_PROMOTION_THRESHOLD,
    MAX_INDEXES_PER_SHEET,
    MAX_STRUCTURAL_CELLS,
    MAX_STRUCTURAL_CHANGE,
    STRUCTURAL_OPERATION_TYPES,
    SumConflictError,
    SumNotFoundError,
    SumPermissionError,
    SumStorageError,
    SumValidationError,
    _WORKBOOK_SESSIONS,
    _column_letter,
)


class SumManagerOperations(SumManagerProtocol):
    def _check_range_permissions(
        self,
        workbook_id: int,
        sheet_id: int,
        cells: List[Dict[str, Any]],
        user_id: int,
    ) -> None:
        workbook = self._db.fetch_one(
            "SELECT owner_id FROM sum_workbooks WHERE id = ?", (int(workbook_id),)
        )
        if workbook and int(workbook["owner_id"]) == int(user_id):
            return
        read_only = self._db.fetch_all(
            "SELECT row_start, row_end, column_start, column_end FROM sum_range_permissions WHERE workbook_id = ? AND sheet_id = ? AND permission = 'read_only'",
            (int(workbook_id), int(sheet_id)),
        )
        if not read_only:
            return
        for cell in cells:
            row = int(cell["row"])
            column = int(cell["column"])
            for rng in read_only:
                if int(rng["row_start"]) <= row < int(rng["row_end"]) and int(
                    rng["column_start"]
                ) <= column < int(rng["column_end"]):
                    raise SumPermissionError(
                        "This range is read-only for your access level"
                    )

    def apply_operation(
        self,
        user_id: int,
        workbook_id: int,
        sheet_id: int,
        operation_id: str,
        operation_type: str,
        base_revision: int,
        cells: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        import utils.logger as logger

        if not operation_id or len(str(operation_id)) > 200:
            raise SumValidationError("operation_id is required")
        if operation_type not in SUM_OPERATION_TYPES:
            raise SumValidationError("Unsupported operation type")
        if operation_type == "rename_sheet":
            raise SumValidationError(
                "rename_sheet is handled by the sheet rename endpoint"
            )
        if (
            not isinstance(cells, list)
            or not cells
            or len(cells) > self.MAX_OPERATION_CELLS
        ):
            raise SumValidationError("Operation must contain 1 to 2000 cells")

        workbook = self._workbook(user_id, workbook_id, mutation=True)
        current_revision = int(workbook["current_revision"])

        # Idempotency check against the main DB log (cheap, no session needed).
        existing_row = self._db.fetch_one(
            "SELECT result_revision FROM sum_operations "
            "WHERE workbook_id = ? AND operation_id = ?",
            (int(workbook_id), str(operation_id)),
        )
        if existing_row is not None:
            return {
                "operation_id": operation_id,
                "revision": int(existing_row["result_revision"]),
                "replayed": True,
            }

        if int(base_revision) != current_revision:
            raise SumConflictError(f"Workbook revision is {current_revision}")

        # Every operation flows through the coalesced live-session path. The
        # removed "solo fast-path" rewrote the entire blob on every operation
        # (one full upload per edit), skipped the durable main-database
        # operation log (breaking idempotent retries, audit, and crash
        # recovery), and bypassed the session/eviction machinery that the
        # revision cadence and multi-writer CAS safety depend on.
        with self._session_lock(int(workbook_id)):
            connection = self._materialized_connection_unlocked(int(workbook_id))
            existing = connection.execute(
                "SELECT result_revision FROM sum_operations WHERE operation_id = ?",
                (str(operation_id),),
            ).fetchone()
            if existing is not None:
                return {
                    "operation_id": operation_id,
                    "revision": int(existing["result_revision"]),
                    "replayed": True,
                }

            if operation_type in STRUCTURAL_OPERATION_TYPES:
                data = self._serialize_connection(connection)
                return self._apply_structural_operation(
                    user_id,
                    workbook_id,
                    sheet_id,
                    operation_id,
                    operation_type,
                    current_revision,
                    cells,
                    data,
                )

        normalized = self._normalize_cells(operation_type, cells)

        revision = current_revision + 1
        now = int(time.time() * 1000)

        with self._session_lock(int(workbook_id)):
            connection = self._materialized_connection_unlocked(int(workbook_id))
            try:
                sheet = connection.execute(
                    "SELECT id FROM sum_sheets WHERE id = ? AND deleted = 0",
                    (str(sheet_id),),
                ).fetchone()
                if sheet is None:
                    raise SumNotFoundError("Sheet not found")
                self._validate_cells(connection, str(sheet_id), normalized)
            except sqlite3.Error as exc:
                raise SumStorageError("Workbook file could not be updated") from exc

            self._check_range_permissions(
                int(workbook_id), int(sheet_id), normalized, user_id
            )

            self._append_op_durable(
                int(workbook_id),
                operation_id,
                user_id,
                current_revision,
                revision,
                operation_type,
                sheet_id,
                normalized,
                now,
                f"Workbook revision is {current_revision}",
            )
            try:
                for cell in normalized:
                    self._apply_normalized_cell(
                        connection, str(sheet_id), cell, revision, now
                    )
                connection.execute(
                    "INSERT INTO sum_operations (operation_id, actor_id, "
                    "base_revision, result_revision, operation_type, "
                    "payload_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        str(operation_id),
                        user_id,
                        current_revision,
                        revision,
                        operation_type,
                        self._json({"sheet_id": str(sheet_id), "cells": normalized}),
                        now,
                    ),
                )
                self._ensure_axis_metadata(connection, str(sheet_id), normalized)
                self._bump_statistics(connection, str(sheet_id), normalized)
                connection.commit()
            except sqlite3.Error as exc:
                self._drop_session(int(workbook_id))
                raise SumStorageError("Workbook file could not be updated") from exc
            session = _WORKBOOK_SESSIONS.get(int(workbook_id))
            if session is not None:
                session.pending += 1
            self._maybe_flush(int(workbook_id))
            # Keep the cached index/calculation status current after every
            # mutation: two COUNTs on the in-memory session plus one main-DB
            # UPDATE. The materialization-time refresh only fires when a
            # session is rebuilt (blob behind the log), which misses the very
            # first buffered operation of a fresh workbook.
            session = _WORKBOOK_SESSIONS.get(int(workbook_id))
            if session is not None:
                try:
                    self._refresh_workbook_status(session.connection, int(workbook_id))
                except Exception:
                    logger.debug(
                        "PlexiSum workbook status refresh failed", exc_info=True
                    )

        self._audit(
            "edit",
            "operation",
            workbook_id,
            user_id,
            {
                "operation_id": str(operation_id),
                "sheet_id": str(sheet_id),
                "revision": revision,
                "operation_type": operation_type,
                "cell_count": len(normalized),
            },
        )
        self._emit_update(workbook, revision, user_id)
        return {
            "operation_id": operation_id,
            "revision": revision,
            "replayed": False,
        }

    def _normalize_cells(
        self, operation_type: str, cells: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Normalize raw operation cells into the canonical internal format."""
        normalized: List[Dict[str, Any]] = []
        for cell in cells:
            try:
                row_index = int(cell["row"])
                column_index = int(cell["column"])
            except (KeyError, TypeError, ValueError) as exc:
                raise SumValidationError(
                    "Each cell needs numeric row and column"
                ) from exc
            if (
                row_index < 0
                or column_index < 0
                or row_index > 10_000_000
                or column_index > 16_384
            ):
                raise SumValidationError("Cell coordinates are out of bounds")
            if operation_type == "clear_cell":
                normalized.append(
                    {
                        "row": row_index,
                        "column": column_index,
                        "value_type": "blank",
                        "value": None,
                        "formula": None,
                        "style": cell.get("style"),
                    }
                )
                continue
            if operation_type == "set_format":
                normalized.append(
                    {
                        "row": row_index,
                        "column": column_index,
                        "value_type": "__preserve__",
                        "value": "__preserve__",
                        "formula": "__preserve__",
                        "style": cell.get("style"),
                    }
                )
                continue
            value_type = str(cell.get("value_type", "blank"))
            if value_type not in SUM_VALUE_TYPES:
                raise SumValidationError("Unknown cell value type")
            normalized.append(
                {
                    "row": row_index,
                    "column": column_index,
                    "value_type": value_type,
                    "value": cell.get("value"),
                    "formula": cell.get("formula"),
                    "style": cell.get("style"),
                }
            )
        return normalized

    # ── structural operations (rows / columns) ───────────────────────────────

    def _apply_structural_operation(
        self,
        user_id: int,
        workbook_id: int,
        sheet_id: int,
        operation_id: str,
        operation_type: str,
        base_revision: int,
        cells: List[Dict[str, Any]],
        data: bytes,
    ) -> Dict[str, Any]:
        """Insert or delete rows/columns as a single revision-bumping operation.

        The payload is an anchor cell plus a count, e.g.
        ``[{"row": 3, "column": 0, "count": 2}]`` inserts two rows at row 3,
        or ``[{"row": 0, "column": 2, "count": 1}]`` deletes one column at
        column 2. Cell coordinates, axis metadata, query statistics, managed
        indexes, and protected ranges are all shifted consistently.
        """
        if not isinstance(cells, list) or not cells:
            raise SumValidationError("Structural operation needs an anchor cell")
        try:
            anchor = cells[0]
            count = int(anchor.get("count", 1))
        except (KeyError, TypeError, ValueError) as exc:
            raise SumValidationError(
                "Structural operation needs a numeric count"
            ) from exc
        if operation_type.endswith("rows"):
            try:
                at = int(anchor["row"])
            except (KeyError, TypeError, ValueError) as exc:
                raise SumValidationError(
                    "Structural operation needs a numeric row anchor"
                ) from exc
            axis = "row"
        else:
            try:
                at = int(anchor["column"])
            except (KeyError, TypeError, ValueError) as exc:
                raise SumValidationError(
                    "Structural operation needs a numeric column anchor"
                ) from exc
            axis = "column"
        if at < 0:
            raise SumValidationError("Anchor index must be non-negative")
        if count < 1 or count > MAX_STRUCTURAL_CHANGE:
            raise SumValidationError(
                f"count must be between 1 and {MAX_STRUCTURAL_CHANGE}"
            )
        self._check_structural_permission(
            int(workbook_id),
            str(sheet_id),
            axis,
            at,
            count,
            operation_type.startswith("insert"),
            user_id,
        )

        now = int(time.time() * 1000)
        connection, path = self._open_sqlite(data)
        try:
            sheet = connection.execute(
                "SELECT id FROM sum_sheets WHERE id = ? AND deleted = 0",
                (str(sheet_id),),
            ).fetchone()
            if sheet is None:
                raise SumNotFoundError("Sheet not found")
            if operation_type == "insert_rows":
                self._shift_axis(connection, str(sheet_id), "row", at, count, True)
            elif operation_type == "delete_rows":
                self._shift_axis(connection, str(sheet_id), "row", at, count, False)
            elif operation_type == "insert_columns":
                self._shift_axis(connection, str(sheet_id), "column", at, count, True)
            else:
                self._shift_axis(connection, str(sheet_id), "column", at, count, False)
            connection.execute(
                "INSERT INTO sum_operations (operation_id, actor_id, base_revision, result_revision, operation_type, payload_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    str(operation_id),
                    user_id,
                    base_revision,
                    base_revision + 1,
                    operation_type,
                    self._json(
                        {
                            "sheet_id": str(sheet_id),
                            "anchor": {"at": at, "count": count, "axis": axis},
                        }
                    ),
                    now,
                ),
            )
            connection.commit()
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            connection.commit()
            with open(path, "rb") as file_handle:
                updated = file_handle.read()
        except sqlite3.Error as exc:
            raise SumStorageError("Workbook file could not be updated") from exc
        finally:
            self._close_sqlite(connection, path)

        revision = self._write_file_revision(
            workbook_id, user_id, operation_type, updated
        )
        self._db.execute(
            "INSERT INTO sum_operations (operation_id, workbook_id, actor_id, base_revision, result_revision, operation_type, payload_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                str(operation_id),
                int(workbook_id),
                user_id,
                base_revision,
                revision,
                operation_type,
                self._json(
                    {
                        "sheet_id": str(sheet_id),
                        "anchor": {"at": at, "count": count, "axis": axis},
                    }
                ),
                now,
            ),
        )
        self._shift_range_permissions(
            int(workbook_id),
            str(sheet_id),
            axis,
            at,
            count,
            operation_type.startswith("insert"),
        )
        return {
            "operation_id": operation_id,
            "revision": revision,
            "replayed": False,
        }

    def _shift_axis(
        self,
        connection: sqlite3.Connection,
        sheet_id: str,
        axis: str,
        at: int,
        count: int,
        insert: bool,
    ) -> None:
        """Shift cells, axis metadata, statistics, and indexes for an insert/delete.

        Uses SQL range UPDATEs for efficient bulk shifts instead of
        read-all/delete/reinsert loops.
        """
        if axis == "row":
            index_field = "row_index"
            axis_table = "sum_rows"
            column_key_field = None
        else:
            index_field = "column_index"
            axis_table = "sum_columns"
            column_key_field = "column_key"

        # 1. Keep the MAX_STRUCTURAL_CELLS check
        source_bound = at if insert else at + count
        count_check = connection.execute(
            f"SELECT COUNT(*) as c FROM sum_cells WHERE sheet_id = ? AND {index_field} >= ?",
            (sheet_id, source_bound),
        ).fetchone()
        if count_check["c"] > MAX_STRUCTURAL_CELLS:
            raise SumValidationError(
                "Operation affects too many cells; split it into smaller changes"
            )

        # 2. Remove the deleted band first (delete ops only).
        if not insert:
            connection.execute(
                f"DELETE FROM sum_cells WHERE sheet_id = ? AND {index_field} >= ? AND {index_field} < ?",
                (sheet_id, at, at + count),
            )

        # 3. Move the surviving cells using SQL range UPDATE.
        if insert:
            connection.execute(
                f"UPDATE sum_cells SET {index_field} = {index_field} + ? "
                f"WHERE sheet_id = ? AND {index_field} >= ?",
                (count, sheet_id, at),
            )
        else:
            connection.execute(
                f"UPDATE sum_cells SET {index_field} = {index_field} - ? "
                f"WHERE sheet_id = ? AND {index_field} >= ?",
                (count, sheet_id, at + count),
            )

        # 4. Axis metadata (sum_rows / sum_columns) stays in sync.
        if insert:
            connection.execute(
                f"UPDATE {axis_table} SET position = position + ? WHERE sheet_id = ? AND position >= ?",
                (count, sheet_id, at),
            )
            now = int(time.time() * 1000)
            rows_data = [
                (str(self._generate_id()), sheet_id, f"{axis}-{i}", i, now)
                for i in range(at, at + count)
            ]
            if axis == "row":
                connection.executemany(
                    "INSERT OR IGNORE INTO sum_rows (id, sheet_id, stable_key, position, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    rows_data,
                )
            else:
                cols_data = [
                    (
                        str(self._generate_id()),
                        sheet_id,
                        f"{axis}-{i}",
                        _column_letter(i),
                        i,
                        now,
                    )
                    for i in range(at, at + count)
                ]
                connection.executemany(
                    "INSERT OR IGNORE INTO sum_columns (id, sheet_id, stable_key, display_name, data_type, position, created_at) "
                    "VALUES (?, ?, ?, ?, 'text', ?, ?)",
                    cols_data,
                )
        else:
            connection.execute(
                f"DELETE FROM {axis_table} WHERE sheet_id = ? AND position >= ? AND position < ?",
                (sheet_id, at, at + count),
            )
            connection.execute(
                f"UPDATE {axis_table} SET position = position - ? WHERE sheet_id = ? AND position >= ?",
                (count, sheet_id, at + count),
            )

        # 5. Query statistics and managed indexes follow column moves.
        if column_key_field:
            if insert:
                connection.execute(
                    "UPDATE sum_statistics SET column_key = CAST(CAST(column_key AS INTEGER) + ? AS TEXT) "
                    "WHERE sheet_id = ? AND CAST(column_key AS INTEGER) >= ?",
                    (count, sheet_id, at),
                )
                connection.execute(
                    "UPDATE sum_indexes SET column_key = CAST(CAST(column_key AS INTEGER) + ? AS TEXT) "
                    "WHERE sheet_id = ? AND CAST(column_key AS INTEGER) >= ?",
                    (count, sheet_id, at),
                )
            else:
                connection.execute(
                    "DELETE FROM sum_statistics WHERE sheet_id = ? "
                    "AND CAST(column_key AS INTEGER) >= ? AND CAST(column_key AS INTEGER) < ?",
                    (sheet_id, at, at + count),
                )
                connection.execute(
                    "UPDATE sum_statistics SET column_key = CAST(CAST(column_key AS INTEGER) - ? AS TEXT) "
                    "WHERE sheet_id = ? AND CAST(column_key AS INTEGER) >= ?",
                    (count, sheet_id, at + count),
                )
                connection.execute(
                    "DELETE FROM sum_indexes WHERE sheet_id = ? "
                    "AND CAST(column_key AS INTEGER) >= ? AND CAST(column_key AS INTEGER) < ?",
                    (sheet_id, at, at + count),
                )
                connection.execute(
                    "UPDATE sum_indexes SET column_key = CAST(CAST(column_key AS INTEGER) - ? AS TEXT) "
                    "WHERE sheet_id = ? AND CAST(column_key AS INTEGER) >= ?",
                    (count, sheet_id, at + count),
                )

        # 6. Column segments follow column moves.
        if column_key_field:
            if insert:
                connection.execute(
                    "UPDATE sum_column_segments SET column_id = CAST(CAST(column_id AS INTEGER) + ? AS TEXT) "
                    "WHERE sheet_id = ? AND CAST(column_id AS INTEGER) >= ?",
                    (count, sheet_id, at),
                )
            else:
                connection.execute(
                    "DELETE FROM sum_column_segments WHERE sheet_id = ? "
                    "AND CAST(column_id AS INTEGER) >= ? AND CAST(column_id AS INTEGER) < ?",
                    (sheet_id, at, at + count),
                )
                connection.execute(
                    "UPDATE sum_column_segments SET column_id = CAST(CAST(column_id AS INTEGER) - ? AS TEXT) "
                    "WHERE sheet_id = ? AND CAST(column_id AS INTEGER) >= ?",
                    (count, sheet_id, at + count),
                )

        # Index entries embed row/column coordinates; invalidate and re-promote.
        self._invalidate_sheet_indexes(connection, sheet_id)

    def _check_structural_permission(
        self,
        workbook_id: int,
        sheet_id: str,
        axis: str,
        at: int,
        count: int,
        insert: bool,
        user_id: int,
    ) -> None:
        """Block deleting cells inside read-only protected ranges."""
        workbook = self._db.fetch_one(
            "SELECT owner_id FROM sum_workbooks WHERE id = ?", (int(workbook_id),)
        )
        if workbook and int(workbook["owner_id"]) == int(user_id):
            return
        if insert:
            return
        read_only = self._db.fetch_all(
            "SELECT row_start, row_end, column_start, column_end FROM sum_range_permissions "
            "WHERE workbook_id = ? AND sheet_id = ? AND permission = 'read_only'",
            (int(workbook_id), int(sheet_id)),
        )
        if not read_only:
            return
        band_start, band_end = at, at + count
        for rng in read_only:
            if axis == "row":
                overlaps = (
                    int(rng["row_start"]) < band_end
                    and int(rng["row_end"]) > band_start
                )
            else:
                overlaps = (
                    int(rng["column_start"]) < band_end
                    and int(rng["column_end"]) > band_start
                )
            if overlaps:
                raise SumPermissionError("Cannot delete cells inside a protected range")

    def _invalidate_sheet_indexes(
        self, connection: sqlite3.Connection, sheet_id: str
    ) -> None:
        """Drop index entries for a sheet and mark indexes pending re-promotion."""
        try:
            connection.execute(
                "DELETE FROM sum_index_entries WHERE index_id IN "
                "(SELECT id FROM sum_indexes WHERE sheet_id = ?)",
                (sheet_id,),
            )
            connection.execute(
                "UPDATE sum_indexes SET state = 'pending' WHERE sheet_id = ?",
                (sheet_id,),
            )
        except sqlite3.Error as exc:
            import utils.logger as logger

            logger.error(
                f"PlexiSum could not invalidate sheet indexes for sheet {sheet_id}: {exc}"
            )
            raise

    def _shift_range_permissions(
        self,
        workbook_id: int,
        sheet_id: str,
        axis: str,
        at: int,
        count: int,
        insert: bool,
    ) -> None:
        """Keep protected ranges aligned with the cells they protect.

        Ranges that fully precede the change stay put; ranges fully after the
        change shift by the delta; ranges that straddle the changed band are
        removed (the safe default for a layout change). Best-effort and
        idempotent; failures only affect range metadata, never cell data.
        """
        try:
            if axis == "row":
                start_field, end_field = "row_start", "row_end"
            else:
                start_field, end_field = "column_start", "column_end"
            if insert:
                self._db.execute(
                    f"UPDATE sum_range_permissions SET {start_field} = {start_field} + ?, {end_field} = {end_field} + ? WHERE workbook_id = ? AND sheet_id = ? AND {start_field} >= ?",
                    (count, count, workbook_id, int(sheet_id), at),
                )
            else:
                band_start, band_end = at, at + count
                # Drop ranges that overlap the deleted band.
                self._db.execute(
                    f"DELETE FROM sum_range_permissions WHERE workbook_id = ? AND sheet_id = ? AND {start_field} < ? AND {end_field} > ?",
                    (workbook_id, int(sheet_id), band_end, band_start),
                )
                # Shift ranges that lie entirely after the band.
                self._db.execute(
                    f"UPDATE sum_range_permissions SET {start_field} = {start_field} - ?, {end_field} = {end_field} - ? WHERE workbook_id = ? AND sheet_id = ? AND {start_field} >= ?",
                    (count, count, workbook_id, int(sheet_id), band_end),
                )
        except Exception:
            import utils.logger as logger

            logger.warning("PlexiSum range-permission shift failed", exc_info=True)

    def _ensure_axis_metadata(
        self,
        connection: sqlite3.Connection,
        sheet_id: str,
        cells: List[Dict[str, Any]],
    ) -> None:
        """Keep the logical ``sum_rows``/``sum_columns`` axis tables in sync.

        The dynamic schema hooks are only useful if they reflect written data;
        this makes the axis metadata real without ever interpolating user
        values into DDL (stable keys are generated, not user-controlled).
        """
        now = int(time.time() * 1000)
        rows_needed: set = set()
        columns_needed: set = set()
        for cell in cells:
            rows_needed.add(int(cell["row"]))
            columns_needed.add(int(cell["column"]))

        if rows_needed:
            rows_data = [
                (str(self._generate_id()), sheet_id, f"row-{r}", r, now)
                for r in rows_needed
            ]
            connection.executemany(
                "INSERT OR IGNORE INTO sum_rows (id, sheet_id, stable_key, position, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                rows_data,
            )

        if columns_needed:
            cols_data = [
                (
                    str(self._generate_id()),
                    sheet_id,
                    f"col-{c}",
                    _column_letter(c),
                    c,
                    now,
                )
                for c in columns_needed
            ]
            connection.executemany(
                "INSERT OR IGNORE INTO sum_columns (id, sheet_id, stable_key, display_name, data_type, position, created_at) "
                "VALUES (?, ?, ?, ?, 'text', ?, ?)",
                cols_data,
            )

    def _bump_statistics(
        self,
        connection: sqlite3.Connection,
        sheet_id: str,
        cells: List[Dict[str, Any]],
    ) -> None:
        """Record per-column access so auto-indexes can be promoted.

        Index promotion is only attempted for columns that cross the access
        threshold during this operation. Previously it ran a full scan of the
        hottest columns on every write, stalling the operation for O(column
        size) work even when nothing new qualified.
        """
        now = int(time.time() * 1000)
        counts: Dict[int, int] = {}
        for cell in cells:
            counts[int(cell["column"])] = counts.get(int(cell["column"]), 0) + 1

        column_keys = list(counts.keys())
        if not column_keys:
            return

        # Batch fetch existing counts
        placeholders = ",".join("?" * len(column_keys))
        existing = connection.execute(
            f"SELECT column_key, access_count FROM sum_statistics "
            f"WHERE sheet_id = ? AND column_key IN ({placeholders})",
            (sheet_id, *[str(k) for k in column_keys]),
        ).fetchall()
        existing_map = {str(r["column_key"]): int(r["access_count"]) for r in existing}

        # Batch upsert
        rows = []
        crossed: List[str] = []
        for column_index, cell_count in counts.items():
            column_key = str(column_index)
            previous = existing_map.get(column_key, 0)
            rows.append((sheet_id, column_key, cell_count, now))
            if previous < INDEX_PROMOTION_THRESHOLD <= previous + cell_count:
                crossed.append(column_key)

        connection.executemany(
            "INSERT INTO sum_statistics (sheet_id, column_key, access_count, updated_at) "
            "VALUES (?, ?, ?, ?) ON CONFLICT(sheet_id, column_key) DO UPDATE SET "
            "access_count = sum_statistics.access_count + excluded.access_count, "
            "updated_at = excluded.updated_at",
            rows,
        )

        if crossed:
            try:
                self._promote_indexes(connection, sheet_id, now, crossed)
            except sqlite3.Error as exc:
                import utils.logger as logger

                logger.error(
                    f"PlexiSum could not promote auto-indexes for sheet {sheet_id}: {exc}"
                )
                raise
        # Re-check pending indexes: a column that crossed the threshold while
        # all its cells were blank is marked 'pending' (its access count never
        # drops below the threshold again, so it cannot "re-cross"). This op
        # may have populated it, so promote it now.
        try:
            pending = [
                str(r["column_key"])
                for r in connection.execute(
                    "SELECT column_key FROM sum_indexes "
                    "WHERE sheet_id = ? AND state = 'pending'",
                    (sheet_id,),
                ).fetchall()
            ]
            if pending:
                self._promote_indexes(connection, sheet_id, now, pending)
        except sqlite3.Error as exc:
            import utils.logger as logger

            logger.error(
                f"PlexiSum could not promote pending auto-indexes for sheet {sheet_id}: {exc}"
            )
            raise

    def _promote_indexes(
        self,
        connection: sqlite3.Connection,
        sheet_id: str,
        now: int,
        columns: List[str],
    ) -> None:
        """Materialize sorted indexes for columns that just crossed the threshold.

        Only the columns passed in are considered (they were measured as newly
        hot in this operation), so steady-state writes never scan columns.
        Index ids use a fresh snowflake to guarantee uniqueness even when two
        promotions happen within the same millisecond.
        """
        existing = {
            str(r["column_key"])
            for r in connection.execute(
                "SELECT column_key FROM sum_indexes WHERE sheet_id = ? AND state IN ('ready', 'building')",
                (sheet_id,),
            ).fetchall()
        }  # Pending markers are placeholders, not materialized indexes, so they
        # must not consume the per-sheet index budget.
        count = connection.execute(
            "SELECT COUNT(*) AS c FROM sum_indexes "
            "WHERE sheet_id = ? AND state != 'pending'",
            (sheet_id,),
        ).fetchone()["c"]
        for column_key in columns:
            if column_key in existing or int(count) >= MAX_INDEXES_PER_SHEET:
                continue
            index_id = f"idx-{sheet_id}-{column_key}-{self._generate_id()}"
            entries = connection.execute(
                "SELECT row_index, value_type, value_json, formula, style_json "
                "FROM sum_cells WHERE sheet_id = ? AND column_index = ? AND value_type != 'blank'",
                (sheet_id, int(column_key)),
            ).fetchall()
            if not entries:
                # No data yet: record a pending marker so a later op that
                # populates the column can promote it. Deleting the marker
                # would make the promotion unreachable because access_count
                # never drops below the threshold again.
                connection.execute(
                    "INSERT OR IGNORE INTO sum_indexes (id, sheet_id, column_key, index_type, state, created_at) VALUES (?, ?, ?, 'sorted', 'pending', ?)",
                    (f"idx-pending-{sheet_id}-{column_key}", sheet_id, column_key, now),
                )
                continue
            connection.execute(
                "INSERT INTO sum_indexes (id, sheet_id, column_key, index_type, state, created_at) VALUES (?, ?, ?, 'sorted', 'building', ?)",
                (index_id, sheet_id, column_key, now),
            )
            connection.executemany(
                "INSERT OR REPLACE INTO sum_index_entries (index_id, value_json, row_index) VALUES (?, ?, ?)",
                [(index_id, r["value_json"], int(r["row_index"])) for r in entries],
            )
            connection.execute(
                "UPDATE sum_indexes SET state = 'ready' WHERE id = ?", (index_id,)
            )
            # A promoted column no longer needs its pending marker.
            connection.execute(
                "DELETE FROM sum_indexes WHERE sheet_id = ? AND column_key = ? AND state = 'pending'",
                (sheet_id, column_key),
            )
            count += 1

            # Pack column into dense segments when it has enough cells
            if len(entries) >= 1000:
                try:
                    segments = SegmentEncoder.pack_column(entries)
                    for seg_id, seg_data in segments:
                        decoded = SegmentEncoder.decode_segment(seg_data)
                        connection.execute(
                            "INSERT OR REPLACE INTO sum_column_segments "
                            "(sheet_id, column_id, segment_id, row_start, row_end, "
                            "encoding, compressed_values, statistics_json) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                            (
                                sheet_id,
                                column_key,
                                seg_id,
                                decoded["row_start"],
                                decoded["row_end"],
                                decoded["encoding"],
                                seg_data,
                                json.dumps(decoded.get("statistics", {})),
                            ),
                        )
                except Exception:
                    pass

    # ── replay / snapshots ────────────────────────────────────────────────────

    def replay_operations(
        self,
        user_id: int,
        workbook_id: int,
        after_revision: int = 0,
        limit: int = 500,
    ) -> List[Dict[str, Any]]:
        limit = max(1, min(int(limit), 1000))
        self._workbook(user_id, workbook_id)
        with self._session_lock(int(workbook_id)):
            connection = self._materialized_connection_unlocked(int(workbook_id))
            try:
                rows = connection.execute(
                    "SELECT operation_id, actor_id, base_revision, result_revision, operation_type, payload_json, created_at FROM sum_operations WHERE result_revision > ? ORDER BY result_revision ASC LIMIT ?",
                    (int(after_revision), limit),
                ).fetchall()
            except sqlite3.Error as exc:
                raise SumStorageError("Workbook file schema is invalid") from exc
        operations = []
        for row in rows:
            payload = self._decode(row["payload_json"], {})
            operations.append(
                {
                    "operation_id": row["operation_id"],
                    "actor_id": str(row["actor_id"]),
                    "base_revision": int(row["base_revision"]),
                    "result_revision": int(row["result_revision"]),
                    "operation_type": row["operation_type"],
                    "sheet_id": payload.get("sheet_id"),
                    "cells": payload.get("cells", []),
                    "created_at": int(row["created_at"]),
                }
            )
        return operations
