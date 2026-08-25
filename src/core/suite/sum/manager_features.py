"""Sum manager mixin: freeze panes, validations, conditional formats, comments (split from manager.py)."""

import json
import sqlite3
import time

from typing import Any, Dict, List, Optional

from .manager_protocol import SumManagerProtocol
from ._shared import (
    SumNotFoundError,
    SumValidationError,
)


class SumManagerFeatures(SumManagerProtocol):
    def set_freeze(
        self,
        user_id: int,
        workbook_id: int,
        sheet_id: int,
        freeze_rows: int = 0,
        freeze_columns: int = 0,
    ) -> Dict[str, Any]:
        """Persist the frozen row/column pane configuration for a sheet."""
        freeze_rows = max(0, int(freeze_rows))
        freeze_columns = max(0, int(freeze_columns))
        if freeze_rows > 500 or freeze_columns > 100:
            raise SumValidationError("Freeze bounds are too large")

        def mutate(connection: sqlite3.Connection) -> None:
            sheet = connection.execute(
                "SELECT id FROM sum_sheets WHERE id = ? AND deleted = 0",
                (str(sheet_id),),
            ).fetchone()
            if sheet is None:
                raise SumNotFoundError("Sheet not found")
            connection.execute(
                "INSERT INTO sum_freeze (sheet_id, freeze_rows, freeze_columns) "
                "VALUES (?, ?, ?) ON CONFLICT(sheet_id) DO UPDATE SET "
                "freeze_rows = excluded.freeze_rows, "
                "freeze_columns = excluded.freeze_columns",
                (str(sheet_id), freeze_rows, freeze_columns),
            )

        self._mutate_file(user_id, int(workbook_id), "set_freeze", mutate)
        return self.get_freeze(user_id, int(workbook_id), int(sheet_id))

    def get_freeze(
        self, user_id: int, workbook_id: int, sheet_id: int
    ) -> Dict[str, Any]:
        """Return the frozen pane configuration for a sheet."""

        def read(connection: sqlite3.Connection) -> Dict[str, Any]:
            row = connection.execute(
                "SELECT freeze_rows, freeze_columns FROM sum_freeze WHERE sheet_id = ?",
                (str(sheet_id),),
            ).fetchone()
            return {
                "sheet_id": str(sheet_id),
                "freeze_rows": int(row["freeze_rows"]) if row else 0,
                "freeze_columns": int(row["freeze_columns"]) if row else 0,
            }

        return self._read_file(user_id, int(workbook_id), read)

    # ── data validation ──────────────────────────────────────────────────────

    VALIDATION_RULE_TYPES = ("whole", "decimal", "list", "length", "date")

    def set_validation(
        self,
        user_id: int,
        workbook_id: int,
        sheet_id: int,
        rule_type: str,
        row_start: int,
        row_end: int,
        column_start: int,
        column_end: int,
        criteria: Dict[str, Any],
        allow_blank: bool = True,
        message: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Add or replace a validation rule over a range (single rule per range)."""
        rule_type = str(rule_type or "").strip()
        if rule_type not in self.VALIDATION_RULE_TYPES:
            raise SumValidationError("Unsupported validation rule type")
        row_start, row_end = int(row_start), int(row_end)
        column_start, column_end = int(column_start), int(column_end)
        if (
            row_start < 0
            or column_start < 0
            or row_end <= row_start
            or column_end <= column_start
            or (row_end - row_start) * (column_end - column_start) > 1_000_000
        ):
            raise SumValidationError("Invalid validation range")
        validation_id = self._generate_id()
        now = int(time.time() * 1000)
        criteria = dict(criteria or {})

        def mutate(connection: sqlite3.Connection) -> None:
            sheet = connection.execute(
                "SELECT id FROM sum_sheets WHERE id = ? AND deleted = 0",
                (str(sheet_id),),
            ).fetchone()
            if sheet is None:
                raise SumNotFoundError("Sheet not found")
            connection.execute(
                "DELETE FROM sum_validations WHERE sheet_id = ? AND row_start = ? "
                "AND row_end = ? AND column_start = ? AND column_end = ?",
                (str(sheet_id), row_start, row_end, column_start, column_end),
            )
            connection.execute(
                "INSERT INTO sum_validations (id, sheet_id, row_start, row_end, "
                "column_start, column_end, rule_type, criteria_json, allow_blank, "
                "message, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    str(validation_id),
                    str(sheet_id),
                    row_start,
                    row_end,
                    column_start,
                    column_end,
                    rule_type,
                    self._json(criteria),
                    1 if allow_blank else 0,
                    message,
                    user_id,
                    now,
                ),
            )

        self._mutate_file(user_id, int(workbook_id), "set_validation", mutate)
        return {
            "id": str(validation_id),
            "sheet_id": str(sheet_id),
            "row_start": row_start,
            "row_end": row_end,
            "column_start": column_start,
            "column_end": column_end,
            "rule_type": rule_type,
            "criteria": criteria,
            "allow_blank": bool(allow_blank),
            "message": message,
        }

    def list_validations(
        self, user_id: int, workbook_id: int, sheet_id: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """List validation rules (optionally for one sheet)."""

        def read(connection: sqlite3.Connection) -> List[Dict[str, Any]]:
            if sheet_id is None:
                rows = connection.execute(
                    "SELECT * FROM sum_validations ORDER BY created_at"
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM sum_validations WHERE sheet_id = ? ORDER BY created_at",
                    (str(sheet_id),),
                ).fetchall()
            return [
                {
                    "id": row["id"],
                    "sheet_id": row["sheet_id"],
                    "row_start": int(row["row_start"]),
                    "row_end": int(row["row_end"]),
                    "column_start": int(row["column_start"]),
                    "column_end": int(row["column_end"]),
                    "rule_type": row["rule_type"],
                    "criteria": self._decode(row["criteria_json"], {}),
                    "allow_blank": bool(row["allow_blank"]),
                    "message": row["message"],
                    "created_at": int(row["created_at"]),
                }
                for row in rows
            ]

        return self._read_file(user_id, int(workbook_id), read)

    def delete_validation(
        self, user_id: int, workbook_id: int, validation_id: str
    ) -> None:
        """Remove a validation rule by id."""

        def mutate(connection: sqlite3.Connection) -> None:
            connection.execute(
                "DELETE FROM sum_validations WHERE id = ?", (str(validation_id),)
            )

        self._mutate_file(user_id, int(workbook_id), "delete_validation", mutate)

    def _validate_cells(
        self,
        connection: sqlite3.Connection,
        sheet_id: str,
        cells: List[Dict[str, Any]],
    ) -> None:
        """Enforce data-validation rules before writes land in the file.

        Blank cells are exempt when the rule allows blanks (clearing a cell is
        always permitted). Formula cells are validated at read time instead,
        since the stored value is the formula text. ``criteria`` shapes:
        ``whole``/``decimal`` -> {min, max}; ``list`` -> {allowed: [...]};
        ``length`` -> {min, max}; ``date`` -> {after, before} (epoch ms).
        """
        rules = connection.execute(
            "SELECT * FROM sum_validations WHERE sheet_id = ?", (sheet_id,)
        ).fetchall()
        if not rules:
            return

        # Build a set of (row, col) -> cell for the cells being written
        cell_map: Dict[tuple, Dict[str, Any]] = {}
        for cell in cells:
            cell_map[(int(cell["row"]), int(cell["column"]))] = cell

        # Fetch existing cells in the affected ranges in ONE query
        if not cell_map:
            return
        rows = [r for r, c in cell_map.keys()]
        cols = [c for r, c in cell_map.keys()]
        min_row, max_row = min(rows), max(rows)
        min_col, max_col = min(cols), max(cols)

        existing = connection.execute(
            "SELECT row_index, column_index, value_type, value_json FROM sum_cells "
            "WHERE sheet_id = ? AND row_index >= ? AND row_index <= ? "
            "AND column_index >= ? AND column_index <= ?",
            (sheet_id, min_row, max_row, min_col, max_col),
        ).fetchall()

        # Merge existing cells into map
        for row in existing:
            key = (int(row["row_index"]), int(row["column_index"]))
            if key not in cell_map:
                cell_map[key] = {
                    "row": key[0],
                    "column": key[1],
                    "value_type": row["value_type"],
                    "value": json.loads(row["value_json"])
                    if row["value_json"]
                    else None,
                }

        # Validate each rule against the cell map
        for rule in rules:
            for (row_index, column_index), cell in cell_map.items():
                if cell.get("value_type") in ("blank", "__preserve__", "formula"):
                    continue
                if not (
                    int(rule["row_start"]) <= row_index < int(rule["row_end"])
                    and int(rule["column_start"])
                    <= column_index
                    < int(rule["column_end"])
                ):
                    continue
                value = cell.get("value")
                if value is None or value == "":
                    if rule["allow_blank"]:
                        continue
                    raise SumValidationError(
                        rule["message"]
                        or "This cell must not be blank (data validation)"
                    )
                criteria = self._decode(rule["criteria_json"], {})
                rule_type = rule["rule_type"]
                message = rule["message"] or "Value does not meet the validation rule"
                if rule_type == "list":
                    allowed = criteria.get("allowed", [])
                    if not (
                        value in allowed or str(value) in [str(a) for a in allowed]
                    ):
                        raise SumValidationError(message)
                elif rule_type in ("whole", "decimal"):
                    try:
                        number = float(value)
                    except (TypeError, ValueError):
                        raise SumValidationError(message)
                    if rule_type == "whole" and number != int(number):
                        raise SumValidationError(message)
                    minimum = criteria.get("min")
                    maximum = criteria.get("max")
                    if minimum is not None and number < float(minimum):
                        raise SumValidationError(message)
                    if maximum is not None and number > float(maximum):
                        raise SumValidationError(message)
                elif rule_type == "length":
                    length = len(str(value))
                    minimum = criteria.get("min")
                    maximum = criteria.get("max")
                    if minimum is not None and length < int(minimum):
                        raise SumValidationError(message)
                    if maximum is not None and length > int(maximum):
                        raise SumValidationError(message)
                elif rule_type == "date":
                    after = criteria.get("after")
                    before = criteria.get("before")
                    try:
                        numeric = (
                            float(value) if isinstance(value, (int, float)) else None
                        )
                    except (TypeError, ValueError):
                        numeric = None
                    if numeric is None:
                        raise SumValidationError(message)
                    if after is not None and numeric < float(after):
                        raise SumValidationError(message)
                    if before is not None and numeric > float(before):
                        raise SumValidationError(message)

    # ── conditional formatting ───────────────────────────────────────────────

    CONDITIONAL_RULE_TYPES = (
        "cell_value",
        "text_contains",
        "formula",
        "blank",
        "duplicate",
    )

    def set_conditional_format(
        self,
        user_id: int,
        workbook_id: int,
        sheet_id: int,
        rule_type: str,
        row_start: int,
        row_end: int,
        column_start: int,
        column_end: int,
        criteria: Dict[str, Any],
        style: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Add a conditional formatting rule over a range."""
        rule_type = str(rule_type or "").strip()
        if rule_type not in self.CONDITIONAL_RULE_TYPES:
            raise SumValidationError("Unsupported conditional format rule type")
        row_start, row_end = int(row_start), int(row_end)
        column_start, column_end = int(column_start), int(column_end)
        if (
            row_start < 0
            or column_start < 0
            or row_end <= row_start
            or column_end <= column_start
        ):
            raise SumValidationError("Invalid conditional format range")
        rule_id = self._generate_id()
        now = int(time.time() * 1000)

        def mutate(connection: sqlite3.Connection) -> None:
            sheet = connection.execute(
                "SELECT id FROM sum_sheets WHERE id = ? AND deleted = 0",
                (str(sheet_id),),
            ).fetchone()
            if sheet is None:
                raise SumNotFoundError("Sheet not found")
            connection.execute(
                "INSERT INTO sum_conditional_formats (id, sheet_id, row_start, "
                "row_end, column_start, column_end, rule_type, criteria_json, "
                "style_json, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    str(rule_id),
                    str(sheet_id),
                    row_start,
                    row_end,
                    column_start,
                    column_end,
                    rule_type,
                    self._json(criteria),
                    self._json(style),
                    user_id,
                    now,
                ),
            )

        self._mutate_file(user_id, int(workbook_id), "set_conditional_format", mutate)
        return {
            "id": str(rule_id),
            "sheet_id": str(sheet_id),
            "row_start": row_start,
            "row_end": row_end,
            "column_start": column_start,
            "column_end": column_end,
            "rule_type": rule_type,
            "criteria": criteria,
            "style": style,
        }

    def list_conditional_formats(
        self, user_id: int, workbook_id: int, sheet_id: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """List conditional formatting rules (optionally for one sheet)."""

        def read(connection: sqlite3.Connection) -> List[Dict[str, Any]]:
            if sheet_id is None:
                rows = connection.execute(
                    "SELECT * FROM sum_conditional_formats ORDER BY created_at"
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM sum_conditional_formats WHERE sheet_id = ? "
                    "ORDER BY created_at",
                    (str(sheet_id),),
                ).fetchall()
            return [
                {
                    "id": row["id"],
                    "sheet_id": row["sheet_id"],
                    "row_start": int(row["row_start"]),
                    "row_end": int(row["row_end"]),
                    "column_start": int(row["column_start"]),
                    "column_end": int(row["column_end"]),
                    "rule_type": row["rule_type"],
                    "criteria": self._decode(row["criteria_json"], {}),
                    "style": self._decode(row["style_json"], {}),
                }
                for row in rows
            ]

        return self._read_file(user_id, int(workbook_id), read)

    def delete_conditional_format(
        self, user_id: int, workbook_id: int, rule_id: str
    ) -> None:
        """Remove a conditional formatting rule by id."""

        def mutate(connection: sqlite3.Connection) -> None:
            connection.execute(
                "DELETE FROM sum_conditional_formats WHERE id = ?", (str(rule_id),)
            )

        self._mutate_file(
            user_id, int(workbook_id), "delete_conditional_format", mutate
        )

    # ── cell comments ────────────────────────────────────────────────────────

    def add_comment(
        self,
        user_id: int,
        workbook_id: int,
        sheet_id: int,
        row_index: int,
        column_index: int,
        body: str,
    ) -> Dict[str, Any]:
        """Add a comment to a cell; @mentions generate notifications."""
        body = str(body or "").strip()
        if not body or len(body) > 5000:
            raise SumValidationError("comment must be between 1 and 5000 characters")
        row_index, column_index = int(row_index), int(column_index)
        if row_index < 0 or column_index < 0:
            raise SumValidationError("Cell coordinates are out of bounds")
        comment_id = self._generate_id()
        now = int(time.time() * 1000)
        workbook = self._workbook(user_id, int(workbook_id), mutation=True)

        def mutate(connection: sqlite3.Connection) -> None:
            sheet = connection.execute(
                "SELECT id FROM sum_sheets WHERE id = ? AND deleted = 0",
                (str(sheet_id),),
            ).fetchone()
            if sheet is None:
                raise SumNotFoundError("Sheet not found")
            connection.execute(
                "INSERT INTO sum_comments (id, sheet_id, row_index, column_index, "
                "author_id, body, resolved, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)",
                (
                    str(comment_id),
                    str(sheet_id),
                    row_index,
                    column_index,
                    user_id,
                    body,
                    now,
                    now,
                ),
            )

        self._mutate_file(user_id, int(workbook_id), "add_comment", mutate)
        comment = {
            "id": str(comment_id),
            "sheet_id": str(sheet_id),
            "row": row_index,
            "column": column_index,
            "author_id": user_id,
            "body": body,
            "resolved": False,
            "created_at": now,
        }
        mentioned = self._notify_comment_mentions(comment, workbook)
        if mentioned:
            try:
                row: Optional[Dict[str, Any]] = self._db.fetch_one(
                    "SELECT * FROM sum_workbooks WHERE id = ?", (int(workbook_id),)
                )
                if row:
                    self._emit_update(
                        row,
                        int(row["current_revision"]),
                        user_id,
                        {
                            "kind": "comment",
                            "comment": {
                                "id": str(comment_id),
                                "row": row_index,
                                "column": column_index,
                                "body": body[:120],
                            },
                            "mentions": mentioned,
                        },
                    )
            except Exception as exc:
                import utils.logger as logger

                logger.error(
                    f"PlexiSum could not emit comment update for workbook "
                    f"{workbook_id}: {exc}"
                )
        return comment

    def list_comments(
        self,
        user_id: int,
        workbook_id: int,
        sheet_id: int,
        row: Optional[int] = None,
        column: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """List comments, optionally for a specific cell."""

        def read(connection: sqlite3.Connection) -> List[Dict[str, Any]]:
            if row is not None and column is not None:
                rows = connection.execute(
                    "SELECT * FROM sum_comments WHERE sheet_id = ? AND row_index = ? "
                    "AND column_index = ? ORDER BY created_at",
                    (str(sheet_id), int(row), int(column)),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM sum_comments WHERE sheet_id = ? ORDER BY created_at",
                    (str(sheet_id),),
                ).fetchall()
            return [
                {
                    "id": row["id"],
                    "sheet_id": row["sheet_id"],
                    "row": int(row["row_index"]),
                    "column": int(row["column_index"]),
                    "author_id": int(row["author_id"]),
                    "body": row["body"],
                    "resolved": bool(row["resolved"]),
                    "created_at": int(row["created_at"]),
                    "updated_at": int(row["updated_at"]),
                }
                for row in rows
            ]

        return self._read_file(user_id, int(workbook_id), read)

    def resolve_comment(
        self, user_id: int, workbook_id: int, comment_id: str, resolved: bool = True
    ) -> None:
        """Mark a comment resolved (or reopen it)."""

        def mutate(connection: sqlite3.Connection) -> None:
            connection.execute(
                "UPDATE sum_comments SET resolved = ?, updated_at = ? WHERE id = ?",
                (1 if resolved else 0, int(time.time() * 1000), str(comment_id)),
            )

        self._mutate_file(user_id, int(workbook_id), "resolve_comment", mutate)

    def delete_comment(self, user_id: int, workbook_id: int, comment_id: str) -> None:
        """Delete a comment."""

        def mutate(connection: sqlite3.Connection) -> None:
            connection.execute(
                "DELETE FROM sum_comments WHERE id = ?", (str(comment_id),)
            )

        self._mutate_file(user_id, int(workbook_id), "delete_comment", mutate)

    # ── XLSX import / export ─────────────────────────────────────────────────
