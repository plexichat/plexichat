"""Sum manager mixin: sort range, CSV import/export, read/mutate helpers (split from manager.py)."""

import sqlite3
import time

from typing import Any, Callable, Dict, List, Optional, Tuple

from .formulas import FormulaEvaluator, is_error
from .manager_protocol import SumManagerProtocol
from ._shared import (
    MAX_CSV_COLUMNS,
    MAX_CSV_ROWS,
    MAX_STRUCTURAL_CELLS,
    SumConflictError,
    SumNotFoundError,
    SumStorageError,
    SumValidationError,
    _classify_csv_value,
)


class SumManagerData(SumManagerProtocol):
    def sort_range(
        self,
        user_id: int,
        workbook_id: int,
        sheet_id: int,
        column: int,
        start_row: int,
        end_row: int,
        direction: str,
        base_revision: int,
    ) -> Dict[str, Any]:
        """Stable-sort a row range by a column's computed value.

        Blank key cells sort last. Formula keys are evaluated live. The whole
        range moves in one file rewrite (bounded), matching the operation
        model used by every other mutation.
        """
        workbook = self._workbook(user_id, workbook_id, mutation=True)
        current_revision = int(workbook["current_revision"])
        if int(base_revision) != current_revision:
            raise SumConflictError(f"Workbook revision is {current_revision}")
        if end_row <= start_row:
            raise SumValidationError("end_row must be greater than start_row")
        if end_row - start_row > MAX_CSV_ROWS:
            raise SumValidationError("Sort range is too large")
        if column < 0 or column > 16_384:
            raise SumValidationError("Column is out of bounds")
        direction = str(direction or "asc").lower()
        if direction not in ("asc", "desc"):
            raise SumValidationError("direction must be asc or desc")

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
            rows = connection.execute(
                "SELECT row_index, column_index, value_type, value_json, formula, style_json, revision, updated_at FROM sum_cells WHERE sheet_id = ? AND row_index >= ? AND row_index < ? ORDER BY row_index, column_index",
                (str(sheet_id), start_row, end_row),
            ).fetchall()
            if len(rows) > MAX_STRUCTURAL_CELLS:
                raise SumValidationError("Sort range affects too many cells")
            by_row: Dict[int, List[Any]] = {}
            for row in rows:
                by_row.setdefault(int(row["row_index"]), []).append(row)
            row_keys = sorted(by_row)
            if len(row_keys) < 2:
                return {
                    "operation_id": f"sort-{time.time_ns()}",
                    "revision": current_revision,
                    "rows": 0,
                }

            resolver = self._resolver_for(connection)
            evaluator = FormulaEvaluator(resolver)

            def sort_key(row_index: int) -> Tuple[int, float, str, int]:
                cell = resolver(str(sheet_id), row_index, column)
                if cell is None:
                    return (2, 0.0, "", row_index)
                formula = cell.get("formula")
                if formula:
                    value = evaluator.evaluate_cell(
                        str(sheet_id), row_index, column, formula
                    )
                    if is_error(value):
                        value = None
                else:
                    value = cell.get("value")
                if isinstance(value, bool):
                    return (0, 1.0 if value else 0.0, "", row_index)
                if isinstance(value, (int, float)):
                    return (0, float(value), "", row_index)
                if value is None or value == "":
                    return (2, 0.0, "", row_index)
                return (1, 0.0, str(value).lower(), row_index)

            def directed_key(row_index: int) -> Tuple[int, float, str, int]:
                # Always sort ascending; only the in-group value component is
                # direction-flipped. Reversing the whole tuple instead would
                # bring the blank group (2) to the top on a descending sort,
                # where Excel keeps blanks last in both directions.
                group, number, text, idx = sort_key(row_index)
                if direction == "desc":
                    if group == 0:
                        return (group, -number, "", idx)
                    if group == 1:
                        # Inverted ordinals make ascending tuple ordering
                        # yield a reverse-lexicographic string order.
                        inverted = "".join(chr(0xFFFF - ord(c)) for c in text)
                        return (group, 0.0, inverted, idx)
                    return (group, 0.0, "", idx)
                return (group, number, text, idx)

            sorted_rows = sorted(row_keys, key=directed_key)
            target: Dict[Tuple[int, int], Any] = {}
            for offset, source_row in enumerate(sorted_rows):
                for cell in by_row[source_row]:
                    target[(start_row + offset, int(cell["column_index"]))] = cell
            connection.execute(
                "DELETE FROM sum_cells WHERE sheet_id = ? AND row_index >= ? AND row_index < ?",
                (str(sheet_id), start_row, end_row),
            )
            connection.executemany(
                "INSERT INTO sum_cells (sheet_id, row_index, column_index, value_type, value_json, formula, style_json, revision, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        str(sheet_id),
                        r,
                        c,
                        cell["value_type"],
                        cell["value_json"],
                        cell["formula"],
                        cell["style_json"],
                        int(cell["revision"]),
                        int(cell["updated_at"]),
                    )
                    for (r, c), cell in target.items()
                ],
            )
            self._invalidate_sheet_indexes(connection, str(sheet_id))
            operation_id = f"sort-{time.time_ns()}"
            connection.execute(
                "INSERT INTO sum_operations (operation_id, actor_id, base_revision, result_revision, operation_type, payload_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    operation_id,
                    user_id,
                    current_revision,
                    current_revision + 1,
                    "sort_range",
                    self._json(
                        {
                            "sheet_id": str(sheet_id),
                            "column": column,
                            "start_row": start_row,
                            "end_row": end_row,
                            "direction": direction,
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
            workbook_id, user_id, "sort_range", updated
        )
        return {
            "operation_id": operation_id,
            "revision": revision,
            "rows": len(row_keys),
        }

    # ── CSV ──────────────────────────────────────────────────────────────────

    def export_csv(
        self, user_id: int, workbook_id: int, sheet_id: int
    ) -> Dict[str, Any]:
        import csv as csv_module
        import io as io_module

        self._workbook(user_id, workbook_id)
        data = self._file_bytes(workbook_id)
        connection, path = self._open_sqlite(data)
        try:
            sheet = connection.execute(
                "SELECT id FROM sum_sheets WHERE id = ? AND deleted = 0",
                (str(sheet_id),),
            ).fetchone()
            if sheet is None:
                raise SumNotFoundError("Sheet not found")
            rows = connection.execute(
                "SELECT row_index, column_index, value_type, value_json, formula FROM sum_cells WHERE sheet_id = ? AND row_index < ? AND column_index < ? ORDER BY row_index, column_index",
                (str(sheet_id), MAX_CSV_ROWS, MAX_CSV_COLUMNS),
            ).fetchall()
        except sqlite3.Error as exc:
            raise SumStorageError("Workbook file schema is invalid") from exc

        def _csv_text(value: Any) -> str:
            # Spreadsheet display convention: whole floats render without a
            # trailing ``.0`` so ``7*3`` exports as ``21``, not ``21.0``.
            if isinstance(value, float) and value.is_integer():
                return str(int(value))
            return str(value)

        try:
            # Formula cells export their computed value (like a spreadsheet),
            # so round-tripping through CSV preserves the numbers users see.
            evaluator = FormulaEvaluator(self._resolver_for(connection))
            formula_cache: Dict[Tuple[int, int], Any] = {}
            grid: Dict[int, Dict[int, str]] = {}
            for cell in rows:
                row_index = int(cell["row_index"])
                column_index = int(cell["column_index"])
                if cell["value_type"] == "formula" and cell["formula"]:
                    key = (row_index, column_index)
                    if key not in formula_cache:
                        formula_cache[key] = evaluator.evaluate_cell(
                            str(sheet_id), row_index, column_index, cell["formula"]
                        )
                    value = formula_cache[key]
                    if is_error(value):
                        continue
                else:
                    value = self._decode(cell["value_json"])
                if value is None:
                    continue
                grid.setdefault(row_index, {})[column_index] = _csv_text(value)
        finally:
            self._close_sqlite(connection, path)
        max_row = max(grid) + 1 if grid else 0
        max_col = max((max(cols) for cols in grid.values()), default=-1) + 1
        buffer = io_module.StringIO()
        writer = csv_module.writer(buffer, lineterminator="\n")
        for row_index in range(max_row):
            writer.writerow(
                [grid.get(row_index, {}).get(col, "") for col in range(max_col)]
            )
        content = buffer.getvalue()
        self._audit(
            "export_csv",
            "sheet",
            sheet_id,
            user_id,
            {"workbook_id": str(workbook_id), "rows": max_row, "columns": max_col},
        )
        return {
            "workbook_id": str(workbook_id),
            "sheet_id": str(sheet_id),
            "filename": f"sheet-{sheet_id}.csv",
            "content": content,
        }

    def import_csv(
        self,
        user_id: int,
        workbook_id: int,
        sheet_id: int,
        content: str,
        base_revision: Optional[int] = None,
    ) -> Dict[str, Any]:
        import csv as csv_module
        import io as io_module

        workbook_row = self._workbook(user_id, workbook_id, mutation=True)
        if base_revision is None:
            # Match upload_file semantics: a null base revision targets the
            # current revision instead of failing the CAS check.
            base_revision = int(workbook_row["current_revision"])
        if not isinstance(content, str) or len(content) > 2_000_000:
            raise SumValidationError("CSV payload is too large")
        reader = csv_module.reader(io_module.StringIO(content))
        cells: List[Dict[str, Any]] = []
        row_count = 0
        for row_index, row in enumerate(reader):
            if row_index >= MAX_CSV_ROWS:
                break
            row_count += 1
            for column_index, value in enumerate(row):
                if column_index >= MAX_CSV_COLUMNS:
                    break
                if value == "":
                    continue
                cells.append(_classify_csv_value(row_index, column_index, value))
        if not cells:
            raise SumValidationError("CSV payload contains no cells")

        if len(cells) <= self.MAX_OPERATION_CELLS:
            # Small imports keep the single-operation path (and its
            # idempotency and per-op audit record).
            operation_id = f"csv-import-{time.time_ns()}"
            result = self.apply_operation(
                user_id,
                workbook_id,
                sheet_id,
                operation_id,
                "set_cell",
                base_revision,
                cells,
            )
            self._audit(
                "import_csv",
                "sheet",
                sheet_id,
                user_id,
                {
                    "workbook_id": str(workbook_id),
                    "operation_id": operation_id,
                    "rows": row_count,
                    "cells": len(cells),
                },
            )
            return {
                "operation_id": operation_id,
                "revision": result["revision"],
                "rows": row_count,
                "cells": len(cells),
            }

        # Large imports: apply the cells in bounded chunks inside a single
        # workbook-file transaction so a million-cell CSV costs one file
        # read + one file write instead of thousands of whole-file rewrites.
        # Each chunk is recorded as its own bounded operation entry, so the
        # operation log and replay stay intact and payloads stay bounded.
        workbook = self._workbook(user_id, workbook_id, mutation=True)
        current_revision = int(workbook["current_revision"])
        if int(base_revision) != current_revision:
            raise SumConflictError(f"Workbook revision is {current_revision}")
        self._check_range_permissions(int(workbook_id), int(sheet_id), cells, user_id)
        # One monotonic revision per chunk keeps every acknowledged operation
        # at a distinct revision (the single-revision-per-chunk shortcut broke
        # replay determinism and the client's one-op-one-revision invariant).
        chunk_count = (
            len(cells) + self.MAX_OPERATION_CELLS - 1
        ) // self.MAX_OPERATION_CELLS
        revision = current_revision + chunk_count
        now = int(time.time() * 1000)
        data = self._file_bytes(workbook_id)
        operation_ids: List[str] = []
        connection, path = self._open_sqlite(data)
        try:
            sheet = connection.execute(
                "SELECT id FROM sum_sheets WHERE id = ? AND deleted = 0",
                (str(sheet_id),),
            ).fetchone()
            if sheet is None:
                raise SumNotFoundError("Sheet not found")
            normalized = [
                {
                    "row": int(cell["row"]),
                    "column": int(cell["column"]),
                    "value_type": str(cell.get("value_type", "text")),
                    "value": cell.get("value"),
                    "formula": cell.get("formula"),
                    "style": cell.get("style"),
                }
                for cell in cells
            ]
            # Same enforcement as the operation path: a chunked import must
            # not bypass data-validation rules set on the target range.
            self._validate_cells(connection, str(sheet_id), normalized)
            base_ts = time.time_ns()
            for chunk_start in range(0, len(normalized), self.MAX_OPERATION_CELLS):
                chunk = normalized[chunk_start : chunk_start + self.MAX_OPERATION_CELLS]
                # Each chunk gets its own monotonic revision so the operation
                # log keeps a strict one-revision-per-op sequence (replay
                # ordering was previously nondeterministic with tied
                # revisions) and cell revisions stay consistent with it.
                chunk_revision = (
                    current_revision + 1 + chunk_start // self.MAX_OPERATION_CELLS
                )
                for cell in chunk:
                    connection.execute(
                        "INSERT INTO sum_cells (sheet_id, row_index, column_index, value_type, value_json, formula, style_json, revision, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(sheet_id, row_index, column_index) DO UPDATE SET value_type = excluded.value_type, value_json = excluded.value_json, formula = excluded.formula, style_json = excluded.style_json, revision = excluded.revision, updated_at = excluded.updated_at",
                        (
                            str(sheet_id),
                            cell["row"],
                            cell["column"],
                            cell["value_type"],
                            self._json(cell["value"]),
                            cell["formula"],
                            self._json(cell["style"]),
                            chunk_revision,
                            now,
                        ),
                    )
                    # Overwriting a coordinate invalidates any packed column
                    # segment covering it, exactly like _apply_normalized_cell
                    # does on the operation path; otherwise stale segment
                    # values resurface through gap-fill merges.
                    self._invalidate_segment(
                        connection, str(sheet_id), int(cell["column"]), int(cell["row"])
                    )
                chunk_index = chunk_start // self.MAX_OPERATION_CELLS
                operation_id = f"csv-import-{base_ts}-{chunk_index}"
                operation_ids.append(operation_id)
                connection.execute(
                    "INSERT INTO sum_operations (operation_id, actor_id, base_revision, result_revision, operation_type, payload_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        operation_id,
                        user_id,
                        current_revision + chunk_index,
                        chunk_revision,
                        "set_cell",
                        self._json({"sheet_id": str(sheet_id), "cells": chunk}),
                        now,
                    ),
                )
            self._ensure_axis_metadata(connection, str(sheet_id), normalized)
            self._bump_statistics(connection, str(sheet_id), normalized)
            connection.commit()
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            connection.commit()
            with open(path, "rb") as file_handle:
                updated = file_handle.read()
        except sqlite3.Error as exc:
            raise SumStorageError("Workbook file could not be updated") from exc
        finally:
            self._close_sqlite(connection, path)

        new_path, size, checksum = self._publish_revision(
            int(workbook_id),
            int(current_revision),
            int(revision),
            updated,
            now,
            "Workbook revision changed during import",
        )
        self._audit(
            "import_csv",
            "sheet",
            sheet_id,
            user_id,
            {
                "workbook_id": str(workbook_id),
                "operation_id": operation_ids[0],
                "operation_count": len(operation_ids),
                "rows": row_count,
                "cells": len(cells),
            },
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
        return {
            "operation_id": operation_ids[0],
            "revision": revision,
            "rows": row_count,
            "cells": len(cells),
        }

    # ── file-backed feature mutations (freeze, validation, formats, comments) ─

    def _read_file(self, user_id: int, workbook_id: int, read: Callable) -> Any:
        """Authorize, open the current file, run a read callback, close it."""
        self._workbook(user_id, workbook_id)
        data = self._file_bytes(workbook_id)
        connection, path = self._open_sqlite(data)
        try:
            return read(connection)
        except sqlite3.Error as exc:
            raise SumStorageError("Workbook file could not be read") from exc
        finally:
            self._close_sqlite(connection, path)

    def _mutate_file(
        self,
        user_id: int,
        workbook_id: int,
        action: str,
        mutate: Callable,
    ) -> int:
        """Open the current file, apply a mutation callback, commit a new revision.

        The callback receives the open connection and must perform its writes;
        the shared commit path bumps the revision, re-caches the bytes, prunes
        superseded blobs, audits, and emits the realtime WORKBOOK_UPDATE event.
        """
        self._workbook(user_id, workbook_id, mutation=True)
        data = self._file_bytes(workbook_id)
        connection, path = self._open_sqlite(data)
        try:
            mutate(connection)
            connection.commit()
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            connection.commit()
            with open(path, "rb") as file_handle:
                updated = file_handle.read()
        except sqlite3.Error as exc:
            raise SumStorageError("Workbook file could not be updated") from exc
        finally:
            self._close_sqlite(connection, path)
        return self._write_file_revision(workbook_id, user_id, action, updated)

    # ── freeze panes ─────────────────────────────────────────────────────────
