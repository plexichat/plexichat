"""PlexiSum durable workbook schema.

The renderer is replaceable; this schema is authoritative. Cells are sparse and
typed, while operations are retained for collaboration/replay. Every read and
write path is bounded by the service layer.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from src.core.database.core.schema_splitter import split_sql_statements

SUM_VALUE_TYPES = ("text", "number", "boolean", "date", "formula", "error", "blank")
SUM_OPERATION_TYPES = ("set_cell", "clear_cell", "set_format", "rename_sheet")

SCHEMA = """
CREATE TABLE IF NOT EXISTS sum_workbooks (
    id BIGINT PRIMARY KEY,
    artifact_id BIGINT,
    owner_id BIGINT NOT NULL,
    title TEXT NOT NULL,
    scope TEXT NOT NULL DEFAULT 'personal',
    server_id BIGINT,
    current_revision BIGINT NOT NULL DEFAULT 0,
    deleted INTEGER NOT NULL DEFAULT 0,
    created_at BIGINT NOT NULL,
    updated_at BIGINT NOT NULL,
    CHECK (scope IN ('personal', 'server')),
    CHECK ((scope = 'personal' AND server_id IS NULL) OR (scope = 'server' AND server_id IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS idx_sum_workbooks_owner ON sum_workbooks(owner_id, deleted, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_sum_workbooks_server ON sum_workbooks(server_id, deleted, updated_at DESC);

CREATE TABLE IF NOT EXISTS sum_sheets (
    id BIGINT PRIMARY KEY,
    workbook_id BIGINT NOT NULL,
    name TEXT NOT NULL,
    position INTEGER NOT NULL DEFAULT 0,
    deleted INTEGER NOT NULL DEFAULT 0,
    created_at BIGINT NOT NULL,
    updated_at BIGINT NOT NULL,
    UNIQUE(workbook_id, name),
    FOREIGN KEY (workbook_id) REFERENCES sum_workbooks(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_sum_sheets_workbook ON sum_sheets(workbook_id, position, deleted);

CREATE TABLE IF NOT EXISTS sum_cells (
    workbook_id BIGINT NOT NULL,
    sheet_id BIGINT NOT NULL,
    row_index INTEGER NOT NULL,
    column_index INTEGER NOT NULL,
    value_type TEXT NOT NULL,
    value_json TEXT,
    formula TEXT,
    style_json TEXT,
    revision BIGINT NOT NULL,
    updated_at BIGINT NOT NULL,
    PRIMARY KEY (workbook_id, sheet_id, row_index, column_index),
    FOREIGN KEY (workbook_id) REFERENCES sum_workbooks(id) ON DELETE CASCADE,
    FOREIGN KEY (sheet_id) REFERENCES sum_sheets(id) ON DELETE CASCADE,
    CHECK (row_index >= 0 AND column_index >= 0)
);
CREATE INDEX IF NOT EXISTS idx_sum_cells_viewport ON sum_cells(workbook_id, sheet_id, row_index, column_index);
CREATE INDEX IF NOT EXISTS idx_sum_cells_revision ON sum_cells(workbook_id, revision);

CREATE TABLE IF NOT EXISTS sum_operations (
    id BIGINT PRIMARY KEY,
    operation_id TEXT NOT NULL UNIQUE,
    workbook_id BIGINT NOT NULL,
    actor_id BIGINT NOT NULL,
    base_revision BIGINT NOT NULL,
    result_revision BIGINT NOT NULL,
    operation_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at BIGINT NOT NULL,
    FOREIGN KEY (workbook_id) REFERENCES sum_workbooks(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_sum_operations_workbook ON sum_operations(workbook_id, result_revision);
"""


def create_tables(db) -> None:
    """Create Sum tables idempotently for the configured database."""
    for statement in split_sql_statements(SCHEMA):
        if statement.strip():
            converted = (
                db.convert_schema(statement)
                if hasattr(db, "convert_schema")
                else statement
            )
            db.execute(converted)


@dataclass(frozen=True)
class SumWorkbook:
    workbook_id: str
    artifact_id: Optional[int]
    owner_id: int
    title: str
    scope: str
    server_id: Optional[int]
    current_revision: int
    sheet_count: int
    created_at: int
    updated_at: int
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SumViewportQuery:
    workbook_id: str
    sheet_id: str
    row_start: int
    row_end: int
    column_start: int
    column_end: int
    known_revision: Optional[int] = None


@dataclass(frozen=True)
class SumCell:
    row_key: str
    column_key: str
    value_type: str
    value: Any = None
    formula: Optional[str] = None
    style_id: Optional[str] = None


@dataclass(frozen=True)
class SumOperationEnvelope:
    operation_id: str
    workbook_id: str
    actor_id: int
    base_revision: int
    result_revision: Optional[int]
    operation_type: str
    payload: Dict[str, Any]
    created_at: int


@dataclass(frozen=True)
class SumViewportResponse:
    workbook_id: str
    sheet_id: str
    revision: int
    cells: List[SumCell] = field(default_factory=list)
    row_count: Optional[int] = None
    column_count: Optional[int] = None
    has_more: bool = False
