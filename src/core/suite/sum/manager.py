"""PlexiSum workbook persistence and authorization service."""

import json
import time
from typing import Any, Dict, List, Optional, Tuple

from src.core.base import BaseManager

from .schema import SUM_OPERATION_TYPES, SUM_VALUE_TYPES


class SumNotFoundError(LookupError):
    """Raised when a workbook or sheet is unavailable."""


class SumPermissionError(PermissionError):
    """Raised when a caller lacks workbook access."""


class SumValidationError(ValueError):
    """Raised for malformed or over-sized workbook operations."""


class SumConflictError(RuntimeError):
    """Raised when an operation's base revision is stale."""


class SumManager(BaseManager):
    """Authoritative bounded data layer for PlexiSum."""

    MAX_VIEWPORT_CELLS = 5000
    MAX_OPERATION_CELLS = 2000

    def __init__(self, db: Any, servers_module: Optional[Any] = None) -> None:
        super().__init__(db)
        self._servers = servers_module

    def _audit(
        self,
        action: str,
        resource_type: str,
        resource_id: Optional[int],
        actor_id: int,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Write a best-effort Suite audit record for Sum mutations."""
        try:
            self._db.execute(
                "INSERT INTO suite_audit_log "
                "(id, domain, action, resource_type, resource_id, actor_id, details_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    self._generate_id(),
                    "sum",
                    action,
                    resource_type,
                    resource_id,
                    actor_id,
                    self._json(details, {}) if details else None,
                    int(time.time() * 1000),
                ),
            )
        except Exception:
            # A missing table on a pre-Suite database must not make a workbook
            # operation fail; production startup creates the table idempotently.
            import utils.logger as logger

            logger.warning("PlexiSum audit write failed", exc_info=True)

    @staticmethod
    def _json(value: Any, fallback: Any = None) -> str:
        try:
            return json.dumps(
                value if value is not None else fallback, separators=(",", ":")
            )
        except (TypeError, ValueError) as exc:
            raise SumValidationError("Value must be JSON serializable") from exc

    @staticmethod
    def _decode(value: Optional[str], fallback: Any = None) -> Any:
        if not value:
            return fallback
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return fallback

    def _is_member(self, user_id: int, server_id: int) -> bool:
        if self._servers is None:
            return False
        try:
            return self._servers.get_member(server_id, user_id) is not None
        except Exception:
            return False

    def _authorize(
        self, workbook: Dict[str, Any], user_id: int, mutation: bool = False
    ) -> None:
        if int(workbook["owner_id"]) == int(user_id):
            return
        server_id = workbook.get("server_id")
        if (
            workbook["scope"] == "server"
            and server_id is not None
            and self._is_member(user_id, int(server_id))
        ):
            if not mutation:
                return
            try:
                if self._servers and self._servers.has_permission(
                    user_id, int(server_id), "server.manage"
                ):
                    return
            except Exception:
                pass
        raise SumPermissionError("Not authorized to access this workbook")

    def _workbook(
        self, user_id: int, workbook_id: int, mutation: bool = False
    ) -> Dict[str, Any]:
        row = self._db.fetch_one(
            "SELECT * FROM sum_workbooks WHERE id = ? AND deleted = 0",
            (int(workbook_id),),
        )
        if not row:
            raise SumNotFoundError("Workbook not found")
        self._authorize(row, user_id, mutation=mutation)
        return row

    def _sheet(
        self, user_id: int, workbook_id: int, sheet_id: int, mutation: bool = False
    ) -> Dict[str, Any]:
        # Authorize workbook access; the return value is not needed here.
        self._workbook(user_id, workbook_id, mutation=mutation)
        row = self._db.fetch_one(
            "SELECT * FROM sum_sheets WHERE id = ? AND workbook_id = ? AND deleted = 0",
            (int(sheet_id), int(workbook_id)),
        )
        if not row:
            raise SumNotFoundError("Sheet not found")
        return row

    def create_workbook(
        self,
        user_id: int,
        title: str,
        scope: str = "personal",
        server_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        title = str(title or "").strip()
        scope = str(scope or "personal").strip().lower()
        if not title or len(title) > 500:
            raise SumValidationError("title must be between 1 and 500 characters")
        if scope not in ("personal", "server"):
            raise SumValidationError("scope must be personal or server")
        if scope == "server" and server_id is None:
            raise SumValidationError("server_id is required for server workbooks")
        if scope == "personal" and server_id is not None:
            raise SumValidationError("personal workbooks cannot include server_id")
        if server_id is not None and not self._is_member(user_id, int(server_id)):
            raise SumPermissionError("You are not a member of this server")
        now = int(time.time() * 1000)
        workbook_id = self._generate_id()
        sheet_id = self._generate_id()
        self._db.begin_transaction()
        try:
            self._db.execute(
                "INSERT INTO sum_workbooks (id, owner_id, title, scope, server_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (workbook_id, user_id, title, scope, server_id, now, now),
            )
            self._db.execute(
                "INSERT INTO sum_sheets (id, workbook_id, name, position, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (sheet_id, workbook_id, "Sheet 1", 0, now, now),
            )
            self._audit(
                "create",
                "workbook",
                workbook_id,
                user_id,
                {"title": title, "scope": scope},
            )
            self._db.commit()
        except Exception:
            if getattr(self._db, "in_transaction", False):
                self._db.rollback()
            raise
        return self.get_workbook(user_id, workbook_id)

    def get_workbook(self, user_id: int, workbook_id: int) -> Dict[str, Any]:
        row = self._workbook(user_id, workbook_id)
        sheets = self._db.fetch_all(
            "SELECT id, name, position, created_at, updated_at FROM sum_sheets WHERE workbook_id = ? AND deleted = 0 ORDER BY position, id",
            (int(workbook_id),),
        )
        return {
            "id": str(row["id"]),
            "artifact_id": str(row["artifact_id"])
            if row.get("artifact_id") is not None
            else None,
            "owner_id": str(row["owner_id"]),
            "title": row["title"],
            "scope": row["scope"],
            "server_id": str(row["server_id"])
            if row.get("server_id") is not None
            else None,
            "current_revision": int(row["current_revision"]),
            "sheets": [
                {
                    "id": str(sheet["id"]),
                    "name": sheet["name"],
                    "position": int(sheet["position"]),
                    "created_at": int(sheet["created_at"]),
                    "updated_at": int(sheet["updated_at"]),
                }
                for sheet in sheets
            ],
            "created_at": int(row["created_at"]),
            "updated_at": int(row["updated_at"]),
        }

    def list_workbooks(
        self, user_id: int, limit: int = 50, offset: int = 0
    ) -> Tuple[List[Dict[str, Any]], int]:
        limit = max(1, min(int(limit), 100))
        offset = max(0, int(offset))
        where = "deleted = 0 AND (owner_id = ? OR (scope = 'server' AND server_id IN (SELECT server_id FROM srv_members WHERE user_id = ?)))"
        params = (user_id, user_id)
        count = self._db.fetch_one(
            f"SELECT COUNT(*) AS count FROM sum_workbooks WHERE {where}", params
        )
        rows = self._db.fetch_all(
            f"SELECT * FROM sum_workbooks WHERE {where} ORDER BY updated_at DESC LIMIT ? OFFSET ?",
            params + (limit, offset),
        )
        items = []
        for row in rows:
            items.append(
                {
                    "id": str(row["id"]),
                    "artifact_id": str(row["artifact_id"])
                    if row.get("artifact_id") is not None
                    else None,
                    "owner_id": str(row["owner_id"]),
                    "title": row["title"],
                    "scope": row["scope"],
                    "server_id": str(row["server_id"])
                    if row.get("server_id") is not None
                    else None,
                    "current_revision": int(row["current_revision"]),
                    "created_at": int(row["created_at"]),
                    "updated_at": int(row["updated_at"]),
                }
            )
        return items, int(count["count"]) if count else 0

    def delete_workbook(self, user_id: int, workbook_id: int) -> None:
        """Soft-delete a workbook while retaining its audit/history rows."""
        # Authorize workbook access; the return value is not needed here.
        self._workbook(user_id, workbook_id, mutation=True)
        now = int(time.time() * 1000)
        self._db.execute(
            "UPDATE sum_workbooks SET deleted = 1, updated_at = ? WHERE id = ?",
            (now, int(workbook_id)),
        )
        self._audit("delete", "workbook", int(workbook_id), user_id)

    def restore_workbook(self, user_id: int, workbook_id: int) -> Dict[str, Any]:
        """Restore a soft-deleted workbook for its owner or server manager."""
        row = self._db.fetch_one(
            "SELECT * FROM sum_workbooks WHERE id = ? AND deleted = 1",
            (int(workbook_id),),
        )
        if not row:
            raise SumNotFoundError("Deleted workbook not found")
        self._authorize(row, user_id, mutation=True)
        now = int(time.time() * 1000)
        self._db.execute(
            "UPDATE sum_workbooks SET deleted = 0, updated_at = ? WHERE id = ?",
            (now, int(workbook_id)),
        )
        self._audit("restore", "workbook", int(workbook_id), user_id)
        return self.get_workbook(user_id, workbook_id)

    def read_viewport(
        self,
        user_id: int,
        workbook_id: int,
        sheet_id: int,
        row_start: int,
        row_end: int,
        column_start: int,
        column_end: int,
    ) -> Dict[str, Any]:
        self._sheet(user_id, workbook_id, sheet_id)
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
        workbook = self._workbook(user_id, workbook_id)
        rows = self._db.fetch_all(
            "SELECT row_index, column_index, value_type, value_json, formula, style_json, revision FROM sum_cells WHERE workbook_id = ? AND sheet_id = ? AND row_index >= ? AND row_index < ? AND column_index >= ? AND column_index < ? ORDER BY row_index, column_index",
            (workbook_id, sheet_id, row_start, row_end, column_start, column_end),
        )
        cells = [
            {
                "row": int(row["row_index"]),
                "column": int(row["column_index"]),
                "value_type": row["value_type"],
                "value": self._decode(row.get("value_json")),
                "formula": row.get("formula"),
                "style": self._decode(row.get("style_json")),
                "revision": int(row["revision"]),
            }
            for row in rows
        ]
        return {
            "workbook_id": str(workbook_id),
            "sheet_id": str(sheet_id),
            "revision": int(workbook["current_revision"]),
            "row_start": row_start,
            "row_end": row_end,
            "column_start": column_start,
            "column_end": column_end,
            "cells": cells,
        }

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
        if not operation_id or len(str(operation_id)) > 200:
            raise SumValidationError("operation_id is required")
        if operation_type not in SUM_OPERATION_TYPES or operation_type != "set_cell":
            raise SumValidationError("Only set_cell is currently supported")
        if (
            not isinstance(cells, list)
            or not cells
            or len(cells) > self.MAX_OPERATION_CELLS
        ):
            raise SumValidationError("Operation must contain 1 to 2000 cells")

        self._db.begin_transaction()
        try:
            workbook = self._workbook(user_id, workbook_id, mutation=True)
            self._sheet(user_id, workbook_id, sheet_id, mutation=True)
            existing = self._db.fetch_one(
                "SELECT workbook_id, result_revision, payload_json FROM sum_operations WHERE operation_id = ?",
                (str(operation_id),),
            )
            if existing:
                if int(existing["workbook_id"]) != int(workbook_id):
                    raise SumConflictError(
                        "operation_id already belongs to another workbook"
                    )
                replayed_sheet = (
                    SumManager._decode(existing["payload_json"], {}).get("sheet_id")
                    if existing["payload_json"]
                    else None
                )
                if replayed_sheet is not None and str(replayed_sheet) != str(sheet_id):
                    raise SumConflictError(
                        "operation_id already belongs to another sheet"
                    )
                result = {
                    "operation_id": operation_id,
                    "revision": int(existing["result_revision"]),
                    "replayed": True,
                }
                self._db.commit()
                return result

            current_revision = int(workbook["current_revision"])
            if int(base_revision) != current_revision:
                raise SumConflictError(f"Workbook revision is {current_revision}")

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

            revision = current_revision + 1
            now = int(time.time() * 1000)

            # Claim the next revision first with a compare-and-swap. Doing this
            # before any cell write means a concurrent writer that raced the
            # same base_revision fails fast, before partial data lands in
            # sum_cells, and the transaction rolls back cleanly.
            revision_cursor = self._db.execute(
                "UPDATE sum_workbooks SET current_revision = ?, updated_at = ? "
                "WHERE id = ? AND current_revision = ?",
                (revision, now, workbook_id, current_revision),
            )
            try:
                affected = int(revision_cursor.rowcount)
            finally:
                revision_cursor.close()
            if affected != 1:
                raise SumConflictError("Workbook revision changed during operation")

            for cell in normalized:
                self._db.upsert(
                    "sum_cells",
                    [
                        "workbook_id",
                        "sheet_id",
                        "row_index",
                        "column_index",
                        "value_type",
                        "value_json",
                        "formula",
                        "style_json",
                        "revision",
                        "updated_at",
                    ],
                    (
                        workbook_id,
                        sheet_id,
                        cell["row"],
                        cell["column"],
                        cell["value_type"],
                        self._json(cell["value"]),
                        cell["formula"],
                        self._json(cell["style"]),
                        revision,
                        now,
                    ),
                    ["workbook_id", "sheet_id", "row_index", "column_index"],
                    [
                        "value_type",
                        "value_json",
                        "formula",
                        "style_json",
                        "revision",
                        "updated_at",
                    ],
                )

            self._db.execute(
                "INSERT INTO sum_operations (id, operation_id, workbook_id, actor_id, base_revision, result_revision, operation_type, payload_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    self._generate_id(),
                    str(operation_id),
                    workbook_id,
                    user_id,
                    current_revision,
                    revision,
                    operation_type,
                    self._json({"sheet_id": str(sheet_id), "cells": normalized}),
                    now,
                ),
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
                    "cell_count": len(normalized),
                },
            )
            self._db.commit()
            return {
                "operation_id": operation_id,
                "revision": revision,
                "replayed": False,
            }
        except Exception:
            if getattr(self._db, "in_transaction", False):
                self._db.rollback()
            raise
