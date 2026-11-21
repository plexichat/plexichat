"""Sum manager mixin: xlsx import/export (split from manager.py)."""

import sqlite3
import time

from typing import Any, Dict, List, Optional

from ..boundaries import (
    MAX_FORMULA_LENGTH,  # noqa: F401 - re-exported for boundary tests
    MAX_XLSX_IMPORT_BYTES,
    bound_formula_text,
    bound_import_bytes,
)
from .manager_protocol import SumManagerProtocol
from ._shared import (
    MAX_CSV_COLUMNS,
    MAX_CSV_ROWS,
    MAX_XLSX_ARCHIVE_ENTRIES,
    MAX_XLSX_EXPANSION_RATIO,
    MAX_XLSX_MEMBER_BYTES,
    MAX_XLSX_UNCOMPRESSED_BYTES,
    XLSX_RATIO_MIN_OUTPUT_BYTES,
    SumNotFoundError,
    SumStorageError,
    SumValidationError,
)


class SumManagerExchange(SumManagerProtocol):
    def export_xlsx(
        self, user_id: int, workbook_id: int, sheet_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """Export the workbook (or one sheet) to an XLSX file.

        The file is produced with ``openpyxl`` in memory, base64-encoded for
        the JSON response (mirroring the ``.plexisum`` download contract),
        and formula cells are written as live formulas.
        """
        import base64 as base64_module
        import io as io_module

        self._workbook(user_id, workbook_id)
        data = self._file_bytes(workbook_id)
        connection, path = self._open_sqlite(data)
        try:
            if sheet_id is None:
                sheets = connection.execute(
                    "SELECT id, name FROM sum_sheets WHERE deleted = 0 ORDER BY position"
                ).fetchall()
            else:
                sheet_row = connection.execute(
                    "SELECT id, name FROM sum_sheets WHERE id = ? AND deleted = 0",
                    (str(sheet_id),),
                ).fetchone()
                if sheet_row is None:
                    raise SumNotFoundError("Sheet not found")
                sheets = [sheet_row]
            rows = connection.execute(
                "SELECT sheet_id, row_index, column_index, value_type, value_json, formula "
                "FROM sum_cells WHERE row_index < ? AND column_index < ? ORDER BY row_index, column_index",
                (MAX_CSV_ROWS, MAX_CSV_COLUMNS),
            ).fetchall()
        except sqlite3.Error as exc:
            raise SumStorageError("Workbook file schema is invalid") from exc

        try:
            import openpyxl
            from openpyxl.cell.cell import MergedCell

            wb = openpyxl.Workbook()
            ws = wb.active
            if ws is None:
                ws = wb.create_sheet()
            for sheet_index, sheet in enumerate(sheets):
                name = str(sheet["name"] or f"Sheet {sheet_index + 1}")
                if sheet_index > 0:
                    ws = wb.create_sheet()
                ws.title = name[:31]
                for cell in rows:
                    if str(cell["sheet_id"]) != str(sheet["id"]):
                        continue
                    row_index = int(cell["row_index"]) + 1
                    column_index = int(cell["column_index"]) + 1
                    target_cell = ws.cell(row=row_index, column=column_index)
                    if target_cell is None or isinstance(target_cell, MergedCell):
                        continue
                    if cell["value_type"] == "formula" and cell["formula"]:
                        target_cell.value = f"={cell['formula']}"
                    else:
                        value = self._decode(cell["value_json"])
                        if value is not None:
                            target_cell.value = value
                            # SECURITY (spreadsheet/formula injection on export):
                            # openpyxl infers a formula for any string cell whose
                            # text starts with "=". A *text* cell can legitimately
                            # hold such a string (the operation write path accepts
                            # arbitrary text values), so writing it verbatim would
                            # make that text execute as a live formula when the
                            # exported workbook is opened. Keep the exact text but
                            # force a string cell. Genuine formula cells (branch
                            # above) are still exported as live formulas.
                            if target_cell.data_type == "f":
                                target_cell.data_type = "s"
            buffer = io_module.BytesIO()
            wb.save(buffer)
            content = base64_module.b64encode(buffer.getvalue()).decode("ascii")
        except ImportError:
            raise SumValidationError(
                "XLSX support requires openpyxl; it is not installed"
            )
        finally:
            self._close_sqlite(connection, path)
        self._audit(
            "export_xlsx",
            "workbook",
            workbook_id,
            user_id,
            {"sheets": len(sheets)},
        )
        return {
            "workbook_id": str(workbook_id),
            "filename": f"{self.get_workbook(user_id, workbook_id)['title'] or 'workbook'}.xlsx",
            "content": content,
            "size": len(base64_module.b64decode(content)),
        }

    def import_xlsx(
        self,
        user_id: int,
        workbook_id: int,
        sheet_id: int,
        content_b64: str,
        base_revision: int,
    ) -> Dict[str, Any]:
        """Import an XLSX file's first sheet into a workbook sheet as cells.

        Values are classified with the same inference used for CSV (numbers,
        booleans, text), and the import is applied through the chunked bulk
        path so a single file rewrite carries the whole payload.
        """
        import base64 as base64_module
        import io as io_module

        self._workbook(user_id, workbook_id, mutation=True)
        if not isinstance(content_b64, str) or not content_b64:
            raise SumValidationError("XLSX content is required")
        if len(content_b64) > MAX_XLSX_IMPORT_BYTES * 2:
            # Refuse an absurd base64 string before allocating the decoded
            # bytes at all.
            raise SumValidationError("XLSX content is too large")
        try:
            raw = base64_module.b64decode(content_b64)
        except Exception:
            raise SumValidationError("XLSX content is not valid base64")
        raw = bound_import_bytes(
            raw,
            max_bytes=MAX_XLSX_IMPORT_BYTES,
            error_cls=SumValidationError,
        )
        self._reject_zip_bomb(raw)
        try:
            import openpyxl

            wb = openpyxl.load_workbook(
                io_module.BytesIO(raw), read_only=True, data_only=False
            )
            ws = wb.active
            if ws is None:
                raise SumValidationError("Workbook has no active sheet")
        except ImportError:
            raise SumValidationError(
                "XLSX support requires openpyxl; it is not installed"
            )
        except SumValidationError:
            raise
        except Exception:
            raise SumValidationError("File is not a valid XLSX workbook")
        cells: List[Dict[str, Any]] = []
        row_count = 0
        for row_index, row in enumerate(ws.iter_rows()):
            if row_index >= MAX_CSV_ROWS:
                break
            row_count += 1
            for column_index, cell in enumerate(row):
                if column_index >= MAX_CSV_COLUMNS:
                    break
                value = cell.value
                if value is None or value == "":
                    continue
                if cell.data_type == "f" and isinstance(value, str):
                    value = bound_formula_text(value, error_cls=SumValidationError)
                    formula = value[1:] if value.startswith("=") else value
                    cells.append(
                        {
                            "row": row_index,
                            "column": column_index,
                            "value_type": "formula",
                            "value": None,
                            "formula": formula,
                        }
                    )
                    continue
                if isinstance(value, bool):
                    cells.append(
                        {
                            "row": row_index,
                            "column": column_index,
                            "value_type": "boolean",
                            "value": value,
                        }
                    )
                elif isinstance(value, (int, float)):
                    cells.append(
                        {
                            "row": row_index,
                            "column": column_index,
                            "value_type": "number",
                            "value": value,
                        }
                    )
                else:
                    cells.append(
                        {
                            "row": row_index,
                            "column": column_index,
                            "value_type": "text",
                            "value": str(value),
                        }
                    )
        if not cells:
            raise SumValidationError("XLSX contains no cells to import")
        if len(cells) <= self.MAX_OPERATION_CELLS:
            operation_id = f"xlsx-import-{time.time_ns()}"
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
                "import_xlsx",
                "sheet",
                sheet_id,
                user_id,
                {"operation_id": operation_id, "rows": row_count, "cells": len(cells)},
            )
            return {
                "operation_id": operation_id,
                "revision": result["revision"],
                "rows": row_count,
                "cells": len(cells),
            }
        # Reuse the chunked CSV bulk path: it is already a single-file
        # transaction that records bounded operation entries per chunk.
        result = self.import_csv(
            user_id,
            workbook_id,
            sheet_id,
            self._cells_to_csv(cells),
            base_revision,
        )
        return {
            "operation_id": result["operation_id"],
            "revision": result["revision"],
            "rows": row_count,
            "cells": len(cells),
        }

    @staticmethod
    def _reject_zip_bomb(raw: bytes) -> None:
        """Reject an XLSX whose declared expansion is implausibly large.

        ``openpyxl`` inflates the archive member by member; a small upload can
        therefore declare a multi-gigabyte worksheet and stall the worker
        before the row/column caps apply. The ZIP central directory carries
        each member's uncompressed size, so the whole expansion is checked
        from the archive metadata without decompressing anything.
        """
        import io as io_module
        import zipfile

        try:
            with zipfile.ZipFile(io_module.BytesIO(raw)) as archive:
                infos = archive.infolist()
        except zipfile.BadZipFile as exc:
            raise SumValidationError("File is not a valid XLSX workbook") from exc
        total = 0
        compressed_total = 0
        for count, info in enumerate(infos, start=1):
            # Count every central-directory record, including directory
            # entries: parsing them into ZipInfo objects is itself the cost a
            # member-count bomb tries to amplify.
            if count > MAX_XLSX_ARCHIVE_ENTRIES:
                raise SumValidationError("XLSX archive has too many entries")
            if info.file_size > MAX_XLSX_MEMBER_BYTES:
                raise SumValidationError("XLSX archive member is too large")
            total += info.file_size
            compressed_total += max(0, info.compress_size)
            if total > MAX_XLSX_UNCOMPRESSED_BYTES:
                raise SumValidationError("XLSX archive expands to too much data")

        # The absolute cap above still allows a ~60 kB upload to declare tens
        # of megabytes of worksheet XML, which openpyxl dutifully inflates and
        # materialises as a single cell before any row/column cap can help.
        # The floor keeps ordinary, modestly compressible workbooks unaffected.
        if (
            total > XLSX_RATIO_MIN_OUTPUT_BYTES
            and compressed_total > 0
            and total // compressed_total > MAX_XLSX_EXPANSION_RATIO
        ):
            raise SumValidationError("XLSX archive expansion ratio is too high")

    @staticmethod
    def _cells_to_csv(cells: List[Dict[str, Any]]) -> str:
        """Render a flat cell list to a minimal CSV grid for the bulk importer."""
        import csv as csv_module
        import io as io_module

        grid: Dict[int, Dict[int, Any]] = {}
        for cell in cells:
            grid.setdefault(int(cell["row"]), {})[int(cell["column"])] = (
                f"={cell['formula']}"
                if cell.get("value_type") == "formula" and cell.get("formula")
                else cell.get("value")
            )
        buffer = io_module.StringIO()
        writer = csv_module.writer(buffer, lineterminator="\n")
        max_row = max(grid) + 1 if grid else 0
        max_col = max((max(cols) for cols in grid.values()), default=-1) + 1
        for row_index in range(max_row):
            writer.writerow(
                [grid.get(row_index, {}).get(col, "") for col in range(max_col)]
            )
        return buffer.getvalue()

    # ── sharing / grants (main-db ACL) ───────────────────────────────────────
