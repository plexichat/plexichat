"""Sum manager mixin: viewport reads and segment merge (split from manager.py)."""

import sqlite3

from typing import Any, Dict, List, Optional, Tuple

from .formulas import FormulaEvaluator, _coerce_number_text, _to_text, is_error
from .segments import SegmentEncoder
from .manager_protocol import SumManagerProtocol
from ._shared import (
    SumNotFoundError,
    SumStorageError,
    SumValidationError,
    _WORKBOOK_SESSIONS,
)


class SumManagerViewport(SumManagerProtocol):
    def _read_viewport_solo(
        self,
        user_id: int,
        workbook_id: int,
        sheet_id: int,
        row_start: int,
        row_end: int,
        column_start: int,
        column_end: int,
        filters: Optional[List[Dict[str, Any]]],
        workbook: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Read viewport directly from the stored blob (no in-memory session).

        Opens the blob through ``_open_sqlite_memory`` (deserialize straight
        into ``:memory:``) instead of writing it to a temp file and reopening
        it on every poll. That removed the per-read disk write + reopen cost
        that solo/read-only viewers paid on each scroll. The caller passes the
        already-authorized workbook row so this path also skips a redundant
        metadata lookup.
        """
        if workbook is None:
            workbook = self._workbook(user_id, workbook_id)
        if not isinstance(workbook, dict):
            raise SumNotFoundError("Workbook not found")
        data = self._blob_bytes(int(workbook_id), workbook)
        connection, path = self._open_sqlite_memory(data)
        try:
            conn = connection

            if (row_end - row_start) * (
                column_end - column_start
            ) > self.MAX_VIEWPORT_CELLS:
                raise SumValidationError("Viewport is too large")

            sheet = conn.execute(
                "SELECT id FROM sum_sheets WHERE id = ? AND deleted = 0",
                (str(sheet_id),),
            ).fetchone()
            if sheet is None:
                raise SumNotFoundError("Sheet not found")

            rows = conn.execute(
                "SELECT row_index, column_index, value_type, value_json, formula, "
                "style_json, revision "
                "FROM sum_cells WHERE sheet_id = ? AND row_index >= ? AND row_index < ? "
                "AND column_index >= ? AND column_index < ? ORDER BY row_index, column_index",
                (str(sheet_id), row_start, row_end, column_start, column_end),
            ).fetchall()

            used = conn.execute(
                "SELECT MAX(row_index) AS max_row, MAX(column_index) AS max_col "
                "FROM sum_cells WHERE sheet_id = ?",
                (str(sheet_id),),
            ).fetchone()

            cells = []
            formula_cells = []
            for row in rows:
                cell_data = {
                    "row": int(row["row_index"]),
                    "column": int(row["column_index"]),
                    "value_type": row["value_type"],
                    "value": self._decode(row["value_json"]),
                    "formula": row["formula"],
                    "style": self._decode(row["style_json"]),
                    "revision": int(row["revision"]),
                }
                if cell_data["value_type"] == "formula" and cell_data["formula"]:
                    formula_cells.append(
                        (cell_data["row"], cell_data["column"], cell_data)
                    )
                cells.append(cell_data)

            if formula_cells:
                from .formulas import FormulaEvaluator

                resolver = self._resolver_for(conn)
                evaluator = FormulaEvaluator(resolver)
                for row_index, column_index, formula_cell in formula_cells:
                    formula_cell["computed"] = evaluator.evaluate_cell(
                        str(sheet_id),
                        row_index,
                        column_index,
                        formula_cell["formula"],
                    )

            max_row = (
                int(used["max_row"]) + 1 if used and used["max_row"] is not None else 0
            )
            max_col = (
                int(used["max_col"]) + 1 if used and used["max_col"] is not None else 0
            )

            hidden_rows = []
            if filters:
                by_row = {}
                for cell in cells:
                    by_row.setdefault(int(cell["row"]), []).append(cell)
                visible = {}
                for row_index in sorted(by_row):
                    row_cells = by_row[row_index]
                    matches = True
                    for criterion in filters:
                        column_index = int(criterion["column"])
                        cell = next(
                            (c for c in row_cells if int(c["column"]) == column_index),
                            None,
                        )
                        value = self._filter_cell_value(cell)
                        if not self._matches_filter(
                            value, criterion["operator"], criterion.get("value")
                        ):
                            matches = False
                            break
                    visible[row_index] = matches
                hidden_rows = [r for r in sorted(by_row) if not visible[r]]

            metadata = self._read_viewport_metadata(
                conn, str(sheet_id), row_start, row_end, column_start, column_end
            )
            viewer = self._viewer_permissions(
                user_id,
                int(workbook_id),
                sheet_id=int(sheet_id),
                workbook=workbook,
            )

            return {
                "workbook_id": str(workbook_id),
                "sheet_id": str(sheet_id),
                "revision": int(workbook["current_revision"]),
                "row_start": row_start,
                "row_end": row_end,
                "column_start": column_start,
                "column_end": column_end,
                "row_count": max_row,
                "column_count": max_col,
                "has_more": bool(row_end < max_row or column_end < max_col),
                "cells": cells,
                "hidden_rows": hidden_rows,
                "filters": filters or [],
                "freeze": metadata["freeze"],
                "validations": metadata["validations"],
                "conditional_formats": metadata["conditional_formats"],
                "comments": metadata["comments"],
                "can_edit": bool(viewer["can_edit"]),
                "read_only_ranges": viewer["read_only_ranges"],
            }
        finally:
            if path is None:
                self._close_sqlite_memory(connection)
            else:
                self._close_sqlite(connection, path)

    @staticmethod
    def _filter_cell_value(cell: Optional[Dict[str, Any]]) -> Any:
        """The comparable value for filter evaluation."""
        if not cell:
            return None
        if cell.get("value_type") == "formula":
            computed = cell.get("computed")
            if is_error(computed):
                return None
            return computed
        return cell.get("value")

    @staticmethod
    def _filter_number(value: Any) -> Optional[float]:
        """The number a filter comparison uses, or ``None`` when there is none.

        A boolean is 1/0, the way Excel compares it numerically everywhere else.
        A string has to read as a whole number: ``float()`` also accepts
        "inf", "nan" and digit underscores, none of which a filter should treat
        as a number, and the client engine reads the same decimal rule rather
        than reaching for ``Number()``, which accepts "0x10" instead.
        """
        if isinstance(value, bool):
            return 1.0 if value else 0.0
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            return _coerce_number_text(value)
        return None

    @classmethod
    def _matches_filter(cls, value: Any, operator: str, target: Any = None) -> bool:
        """Evaluate one filter criterion against a cell's comparable value."""
        op = str(operator or "").strip()
        if op == "is_empty":
            return value is None or value == ""
        if op == "is_not_empty":
            return not (value is None or value == "")
        # ``str(target or "")`` turned a legitimate 0 or False search term into
        # the empty string, so ``contains 0`` matched every non-empty cell and
        # ``not_contains 0`` matched none of them. Only a missing target is
        # empty text (the client engine compares with the same rule).
        #
        # ``_to_text`` rather than ``str``: str(2.0) is "2.0", so a filter for
        # "2" missed every computed cell, while the client engine's String(2)
        # is "2". Booleans print as TRUE/FALSE here and true/false there, so
        # both sides fold case.
        needle = "" if target is None else _to_text(target).casefold()
        haystack = "" if value is None else _to_text(value).casefold()
        if op == "contains":
            return value is not None and needle in haystack
        if op == "not_contains":
            return not (value is not None and needle in haystack)
        if value is None:
            return False
        if op in ("eq", "neq"):
            # Two operands that both read as numbers are compared as numbers,
            # even when one of them arrived as text: the filter panel sends the
            # value it was given, so "equals 2" has to match 2.0.
            number = cls._filter_number(value)
            target_number = cls._filter_number(target)
            if number is not None and target_number is not None:
                equal = number == target_number
            else:
                equal = _to_text(value).casefold() == _to_text(target).casefold()
            return equal if op == "eq" else not equal
        # The four ordering operators need a number on both sides; anything
        # else (a blank, "abc", "inf", "0x10") has nothing to compare.
        number = cls._filter_number(value)
        target_number = cls._filter_number(target)
        if number is None or target_number is None:
            return False
        if op == "gt":
            return number > target_number
        if op == "gte":
            return number >= target_number
        if op == "lt":
            return number < target_number
        if op == "lte":
            return number <= target_number
        # Fail closed: read_viewport validates the operator before it calls
        # here, so an unknown one means "no match" rather than "matches
        # everything" (the client engine behaves the same way).
        return False

    def _read_viewport_metadata(
        self,
        connection: sqlite3.Connection,
        sheet_id: str,
        row_start: int,
        row_end: int,
        column_start: int,
        column_end: int,
        max_comments: int = 200,
    ) -> Dict[str, Any]:
        """Freeze config, validations, conditional formats, and viewport comments.

        All four are bounded: validations and conditional formats are capped to
        the first 200 rules for the sheet, and comments to the first 200 in the
        viewport range (enough for a markers overlay). The caller renders rules
        client-side against the same cell coordinates.
        """
        metadata: Dict[str, Any] = {
            "freeze": {"freeze_rows": 0, "freeze_columns": 0},
            "validations": [],
            "conditional_formats": [],
            "comments": [],
        }
        try:
            freeze = connection.execute(
                "SELECT freeze_rows, freeze_columns FROM sum_freeze WHERE sheet_id = ?",
                (sheet_id,),
            ).fetchone()
            if freeze:
                metadata["freeze"] = {
                    "freeze_rows": int(freeze["freeze_rows"]),
                    "freeze_columns": int(freeze["freeze_columns"]),
                }
        except sqlite3.Error as exc:
            import utils.logger as logger

            logger.error(
                f"PlexiSum viewport could not read freeze config for sheet {sheet_id}: {exc}"
            )
        try:
            validations = connection.execute(
                "SELECT id, row_start, row_end, column_start, column_end, rule_type, "
                "criteria_json, allow_blank, message FROM sum_validations "
                "WHERE sheet_id = ? LIMIT 200",
                (sheet_id,),
            ).fetchall()
            metadata["validations"] = [
                {
                    "id": str(v["id"]),
                    "row_start": int(v["row_start"]),
                    "row_end": int(v["row_end"]),
                    "column_start": int(v["column_start"]),
                    "column_end": int(v["column_end"]),
                    "rule_type": v["rule_type"],
                    "criteria": self._decode(v["criteria_json"], {}),
                    "allow_blank": bool(v["allow_blank"]),
                    "message": v["message"],
                }
                for v in validations
            ]
        except sqlite3.Error as exc:
            import utils.logger as logger

            logger.error(
                f"PlexiSum viewport could not read validations for sheet {sheet_id}: {exc}"
            )
        try:
            formats = connection.execute(
                "SELECT id, row_start, row_end, column_start, column_end, rule_type, "
                "criteria_json, style_json FROM sum_conditional_formats "
                "WHERE sheet_id = ? LIMIT 200",
                (sheet_id,),
            ).fetchall()
            metadata["conditional_formats"] = [
                {
                    "id": str(f["id"]),
                    "row_start": int(f["row_start"]),
                    "row_end": int(f["row_end"]),
                    "column_start": int(f["column_start"]),
                    "column_end": int(f["column_end"]),
                    "rule_type": f["rule_type"],
                    "criteria": self._decode(f["criteria_json"], {}),
                    "style": self._decode(f["style_json"], {}),
                }
                for f in formats
            ]
        except sqlite3.Error as exc:
            import utils.logger as logger

            logger.error(
                f"PlexiSum viewport could not read conditional formats for sheet {sheet_id}: {exc}"
            )
        try:
            comments = connection.execute(
                "SELECT id, row_index, column_index, author_id, body, resolved, "
                "created_at FROM sum_comments WHERE sheet_id = ? AND row_index >= ? "
                "AND row_index < ? AND column_index >= ? AND column_index < ? "
                "ORDER BY created_at LIMIT ?",
                (sheet_id, row_start, row_end, column_start, column_end, max_comments),
            ).fetchall()
            metadata["comments"] = [
                {
                    "id": str(c["id"]),
                    "row": int(c["row_index"]),
                    "column": int(c["column_index"]),
                    "author_id": int(c["author_id"]),
                    "body": c["body"],
                    "resolved": bool(c["resolved"]),
                    "created_at": int(c["created_at"]),
                }
                for c in comments
            ]
        except sqlite3.Error as exc:
            import utils.logger as logger

            logger.error(
                f"PlexiSum viewport could not read comments for sheet {sheet_id}: {exc}"
            )
        return metadata

    def read_viewport(
        self,
        user_id: int,
        workbook_id: int,
        sheet_id: int,
        row_start: int,
        row_end: int,
        column_start: int,
        column_end: int,
        filters: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        workbook = self._workbook(user_id, workbook_id)
        row_start, row_end = int(row_start), int(row_end)
        column_start, column_end = int(column_start), int(column_end)
        if (
            row_start < 0
            or column_start < 0
            or row_end <= row_start
            or column_end <= column_start
        ):
            raise SumValidationError("Invalid viewport bounds")
        if (row_end - row_start) * (
            column_end - column_start
        ) > self.MAX_VIEWPORT_CELLS:
            raise SumValidationError("Viewport is too large")
        normalized_filters: List[Dict[str, Any]] = []
        if filters:
            for criterion in filters[:50]:
                try:
                    column_index = int(criterion["column"])
                except (KeyError, TypeError, ValueError):
                    continue
                if column_index < 0 or column_index > 16_384:
                    continue
                operator = str(criterion.get("operator", "eq")).strip()
                if operator not in (
                    "eq",
                    "neq",
                    "gt",
                    "gte",
                    "lt",
                    "lte",
                    "contains",
                    "not_contains",
                    "is_empty",
                    "is_not_empty",
                ):
                    continue
                normalized_filters.append(
                    {
                        "column": column_index,
                        "operator": operator,
                        "value": criterion.get("value"),
                    }
                )
        # Solo fast-path: read directly from the stored blob without a
        # persistent session. Only valid when there is NO live session AND the
        # stored blob is fully current — an existing session, or unflushed
        # operations in the durable log (blob_revision behind
        # current_revision, e.g. after a session was dropped), means the blob
        # would serve stale data and the read must go through the session,
        # which replays the log on materialization.
        if (
            self._is_solo(int(workbook_id))
            and int(workbook_id) not in _WORKBOOK_SESSIONS
        ):
            _rev_row = self._db.fetch_one(
                "SELECT blob_revision, current_revision FROM sum_workbooks "
                "WHERE id = ?",
                (int(workbook_id),),
            )
            if _rev_row is not None and int(_rev_row["blob_revision"]) >= int(
                _rev_row["current_revision"]
            ):
                return self._read_viewport_solo(
                    user_id,
                    workbook_id,
                    sheet_id,
                    row_start,
                    row_end,
                    column_start,
                    column_end,
                    normalized_filters,
                    workbook=workbook,
                )

        # Query the live session directly: hot viewport polls must not pay a
        # serialize + deserialize of the whole workbook per request. The
        # per-workbook lock is held for the duration of the read so the
        # connection cannot be dropped or mutated mid-query.
        _session_lock = self._session_lock(int(workbook_id))
        _session_lock.acquire()
        try:
            connection = self._materialized_connection_unlocked(int(workbook_id))
            sheet = connection.execute(
                "SELECT id FROM sum_sheets WHERE id = ? AND deleted = 0",
                (str(sheet_id),),
            ).fetchone()
            if sheet is None:
                raise SumNotFoundError("Sheet not found")
            rows = connection.execute(
                "SELECT row_index, column_index, value_type, value_json, formula, style_json, revision FROM sum_cells WHERE sheet_id = ? AND row_index >= ? AND row_index < ? AND column_index >= ? AND column_index < ? ORDER BY row_index, column_index",
                (str(sheet_id), row_start, row_end, column_start, column_end),
            ).fetchall()
            # Check for segment-backed columns and merge with sparse cells
            segment_map: Dict[int, Dict[int, Dict[str, Any]]] = {}
            for col_idx in range(column_start, column_end):
                try:
                    seg_rows = connection.execute(
                        "SELECT segment_id, compressed_values FROM sum_column_segments "
                        "WHERE sheet_id = ? AND column_id = ? AND row_end > ? AND row_start < ?",
                        (str(sheet_id), str(col_idx), row_start, row_end),
                    ).fetchall()
                    if seg_rows:
                        col_cells = {}
                        for seg_row in seg_rows:
                            decoded = SegmentEncoder.decode_segment(
                                seg_row["compressed_values"]
                            )
                            for cell in decoded.get("cells", []) or []:
                                if not isinstance(cell, dict):
                                    continue
                                r = int(cell.get("row_index", cell.get("row", 0)))
                                if row_start <= r < row_end:
                                    col_cells[r] = cell
                        segment_map[col_idx] = col_cells
                except Exception:
                    pass
            # Merge: segment cells fill gaps, sparse cells take precedence.
            # Merged rows are plain dicts keyed like the sqlite rows
            # (subscriptable via ``row["column"]``); the merged list is
            # re-sorted to preserve (row, column) ordering.
            if segment_map:
                existing_keys = set()
                merged_rows: List[Dict[str, Any]] = []
                for row in rows:
                    r, c = int(row["row_index"]), int(row["column_index"])
                    existing_keys.add((r, c))
                    merged_rows.append(
                        {
                            "row_index": r,
                            "column_index": c,
                            "value_type": row["value_type"],
                            "value_json": row["value_json"],
                            "formula": row["formula"],
                            "style_json": row["style_json"],
                            "revision": int(row["revision"]),
                        }
                    )
                for col_idx, col_cells in segment_map.items():
                    for r, cell in col_cells.items():
                        if (r, col_idx) not in existing_keys:
                            merged_rows.append(
                                {
                                    "row_index": int(r),
                                    "column_index": int(col_idx),
                                    "value_type": cell.get("value_type", "blank"),
                                    "value_json": cell.get("value_json"),
                                    "formula": cell.get("formula"),
                                    "style_json": cell.get("style_json"),
                                    "revision": 0,
                                }
                            )
                merged_rows.sort(
                    key=lambda item: (item["row_index"], item["column_index"])
                )
                rows = merged_rows
            used = connection.execute(
                "SELECT MAX(row_index) AS max_row, MAX(column_index) AS max_col FROM sum_cells WHERE sheet_id = ?",
                (str(sheet_id),),
            ).fetchone()

            cells: List[Dict[str, Any]] = []
            formula_cells: List[Tuple[int, int, Dict[str, Any]]] = []
            for row in rows:
                cell_data: Dict[str, Any] = {
                    "row": int(row["row_index"]),
                    "column": int(row["column_index"]),
                    "value_type": row["value_type"],
                    "value": self._decode(row["value_json"]),
                    "formula": row["formula"],
                    "style": self._decode(row["style_json"]),
                    "revision": int(row["revision"]),
                }
                if cell_data["value_type"] == "formula" and cell_data["formula"]:
                    formula_cells.append(
                        (cell_data["row"], cell_data["column"], cell_data)
                    )
                cells.append(cell_data)

            if formula_cells:
                resolver = self._resolver_for(connection)
                evaluator = FormulaEvaluator(resolver)
                for row_index, column_index, formula_cell in formula_cells:
                    formula_cell["computed"] = evaluator.evaluate_cell(
                        str(sheet_id), row_index, column_index, formula_cell["formula"]
                    )
            max_row = (
                int(used["max_row"]) + 1 if used and used["max_row"] is not None else 0
            )
            max_col = (
                int(used["max_col"]) + 1 if used and used["max_col"] is not None else 0
            )

            # Server-side filtering
            hidden_rows: List[int] = []
            if normalized_filters:
                by_row: Dict[int, List[Dict[str, Any]]] = {}
                for cell in cells:
                    by_row.setdefault(int(cell["row"]), []).append(cell)
                visible: Dict[int, bool] = {}
                for row_index in sorted(by_row):
                    row_cells = by_row[row_index]
                    matches = True
                    for criterion in normalized_filters:
                        column_index = int(criterion["column"])
                        cell: Optional[Dict[str, Any]] = next(
                            (c for c in row_cells if int(c["column"]) == column_index),
                            None,
                        )
                        value = self._filter_cell_value(cell)
                        if not self._matches_filter(
                            value, criterion["operator"], criterion.get("value")
                        ):
                            matches = False
                            break
                    visible[row_index] = matches
                hidden_rows = [
                    row_index for row_index in sorted(by_row) if not visible[row_index]
                ]

            metadata = self._read_viewport_metadata(
                connection,
                str(sheet_id),
                row_start,
                row_end,
                column_start,
                column_end,
            )
            try:
                session = _WORKBOOK_SESSIONS.get(int(workbook_id))
                if session is None or session.pending > 0:
                    self._refresh_workbook_status(connection, int(workbook_id))
            except Exception:
                pass
        except sqlite3.Error as exc:
            raise SumStorageError("Workbook file schema is invalid") from exc
        finally:
            _session_lock.release()
        viewer = self._viewer_permissions(
            user_id, int(workbook_id), sheet_id=int(sheet_id), workbook=workbook
        )
        return {
            "workbook_id": str(workbook_id),
            "sheet_id": str(sheet_id),
            "revision": int(workbook["current_revision"]),
            "row_start": row_start,
            "row_end": row_end,
            "column_start": column_start,
            "column_end": column_end,
            "row_count": max_row,
            "column_count": max_col,
            "has_more": bool(row_end < max_row or column_end < max_col),
            "cells": cells,
            "hidden_rows": hidden_rows,
            "filters": normalized_filters,
            "freeze": metadata["freeze"],
            "validations": metadata["validations"],
            "conditional_formats": metadata["conditional_formats"],
            "comments": metadata["comments"],
            "can_edit": bool(viewer["can_edit"]),
            "read_only_ranges": viewer["read_only_ranges"],
        }

    # ── operations ───────────────────────────────────────────────────────────
