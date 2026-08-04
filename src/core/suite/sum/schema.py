"""PlexiSum durable schema: metadata in the main database, data in .plexisum files.

A PlexiSum workbook is an independent SQLite database file (``.plexisum``)
stored through the main file-storage adapter (S3/local). The main database
holds only the identity, ownership, sharing ACL, range permissions, snapshot
pointers, and revision counter — never the workbook cells.

The file schema is dynamic: the logical column/row layout lives in
``sum_columns``/``sum_rows`` inside the workbook file while the physical tables
remain stable. Cells are typed and sparse. Managed indexes are created from
measured workloads (``sum_statistics`` drives ``sum_indexes``).
"""

import sqlite3
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from src.core.database.core.schema_splitter import split_sql_statements

SUM_VALUE_TYPES = ("text", "number", "boolean", "date", "formula", "error", "blank")
SUM_OPERATION_TYPES = (
    "set_cell",
    "clear_cell",
    "set_format",
    "rename_sheet",
    "insert_rows",
    "delete_rows",
    "insert_columns",
    "delete_columns",
)

# The workbook file format marker. Bump when the on-disk file schema changes.
PLEXISUM_FORMAT_VERSION = "1"
SUPPORTED_PLEXISUM_FORMAT_VERSIONS = frozenset({PLEXISUM_FORMAT_VERSION})

# ── Main-database metadata (never the workbook data) ─────────────────────────

MAIN_SCHEMA = """
CREATE TABLE IF NOT EXISTS sum_workbooks (
    id BIGINT PRIMARY KEY,
    artifact_id BIGINT,
    owner_id BIGINT NOT NULL,
    title TEXT NOT NULL,
    scope TEXT NOT NULL DEFAULT 'personal',
    server_id BIGINT,
    current_revision BIGINT NOT NULL DEFAULT 0,
    blob_path TEXT,
    file_size BIGINT NOT NULL DEFAULT 0,
    checksum TEXT,
    deleted INTEGER NOT NULL DEFAULT 0,
    created_at BIGINT NOT NULL,
    updated_at BIGINT NOT NULL,
    CHECK (scope IN ('personal', 'server')),
    CHECK ((scope = 'personal' AND server_id IS NULL) OR (scope = 'server' AND server_id IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS idx_sum_workbooks_owner ON sum_workbooks(owner_id, deleted, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_sum_workbooks_server ON sum_workbooks(server_id, deleted, updated_at DESC);

CREATE TABLE IF NOT EXISTS sum_grants (
    id BIGINT PRIMARY KEY,
    workbook_id BIGINT NOT NULL,
    subject_id BIGINT NOT NULL,
    permissions TEXT NOT NULL,
    expires_at BIGINT,
    revoked_at BIGINT,
    created_by BIGINT NOT NULL,
    created_at BIGINT NOT NULL,
    FOREIGN KEY (workbook_id) REFERENCES sum_workbooks(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_sum_grants_workbook ON sum_grants(workbook_id, revoked_at);
CREATE INDEX IF NOT EXISTS idx_sum_grants_subject ON sum_grants(subject_id, revoked_at);

CREATE TABLE IF NOT EXISTS sum_range_permissions (
    id BIGINT PRIMARY KEY,
    workbook_id BIGINT NOT NULL,
    sheet_id BIGINT NOT NULL,
    row_start INTEGER NOT NULL,
    row_end INTEGER NOT NULL,
    column_start INTEGER NOT NULL,
    column_end INTEGER NOT NULL,
    permission TEXT NOT NULL CHECK (permission IN ('read_only', 'editable')),
    created_by BIGINT NOT NULL,
    created_at BIGINT NOT NULL,
    FOREIGN KEY (workbook_id) REFERENCES sum_workbooks(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_sum_range_permissions_sheet ON sum_range_permissions(sheet_id);

CREATE TABLE IF NOT EXISTS sum_snapshots (
    id BIGINT PRIMARY KEY,
    workbook_id BIGINT NOT NULL,
    created_by BIGINT NOT NULL,
    revision BIGINT NOT NULL,
    blob_path TEXT,
    checksum TEXT,
    created_at BIGINT NOT NULL,
    FOREIGN KEY (workbook_id) REFERENCES sum_workbooks(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_sum_snapshots_workbook ON sum_snapshots(workbook_id, created_at DESC);
"""

# Required columns are a compact compatibility contract for existing files.
# ``CREATE TABLE IF NOT EXISTS`` can add missing tables but cannot repair a
# table whose columns were changed by an incompatible external editor. Such a
# file is rejected rather than being opened with silently incorrect semantics.
WORKBOOK_SCHEMA_REQUIREMENTS = {
    "sum_meta": {"key", "value"},
    "sum_sheets": {"id", "name", "position", "deleted", "created_at", "updated_at"},
    "sum_columns": {
        "id",
        "sheet_id",
        "stable_key",
        "display_name",
        "data_type",
        "position",
    },
    "sum_rows": {"id", "sheet_id", "stable_key", "position", "created_at"},
    "sum_cells": {
        "sheet_id",
        "row_index",
        "column_index",
        "value_type",
        "value_json",
        "formula",
        "style_json",
        "revision",
        "updated_at",
    },
    "sum_operations": {
        "operation_id",
        "actor_id",
        "base_revision",
        "result_revision",
        "operation_type",
        "payload_json",
        "created_at",
    },
    "sum_snapshots": {
        "snapshot_id",
        "revision",
        "snapshot_json",
        "checksum",
        "created_at",
    },
    "sum_statistics": {
        "sheet_id",
        "column_key",
        "filter_count",
        "sort_count",
        "access_count",
        "updated_at",
    },
    "sum_indexes": {
        "id",
        "sheet_id",
        "column_key",
        "index_type",
        "state",
        "statistics_json",
        "created_at",
    },
    "sum_index_entries": {"index_id", "value_json", "row_index"},
    "sum_validations": {
        "id",
        "sheet_id",
        "row_start",
        "row_end",
        "column_start",
        "column_end",
        "rule_type",
        "criteria_json",
        "allow_blank",
        "message",
        "created_by",
        "created_at",
    },
    "sum_conditional_formats": {
        "id",
        "sheet_id",
        "row_start",
        "row_end",
        "column_start",
        "column_end",
        "rule_type",
        "criteria_json",
        "style_json",
        "created_by",
        "created_at",
    },
    "sum_comments": {
        "id",
        "sheet_id",
        "row_index",
        "column_index",
        "author_id",
        "body",
        "resolved",
        "created_at",
        "updated_at",
    },
    "sum_freeze": {"sheet_id", "freeze_rows", "freeze_columns"},
}


def create_tables(db) -> None:
    """Create the main-database Sum metadata tables idempotently."""
    for statement in split_sql_statements(MAIN_SCHEMA):
        if statement.strip():
            converted = (
                db.convert_schema(statement)
                if hasattr(db, "convert_schema")
                else statement
            )
            db.execute(converted)


# ── The .plexisum workbook file schema (dynamic, data-carrying) ──────────────

WORKBOOK_FILE_SCHEMA = """
CREATE TABLE IF NOT EXISTS sum_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sum_sheets (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    position INTEGER NOT NULL DEFAULT 0,
    deleted INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS sum_columns (
    id TEXT PRIMARY KEY,
    sheet_id TEXT NOT NULL,
    stable_key TEXT NOT NULL,
    display_name TEXT NOT NULL,
    data_type TEXT NOT NULL DEFAULT 'text',
    position INTEGER NOT NULL DEFAULT 0,
    width INTEGER,
    format TEXT,
    nullable INTEGER NOT NULL DEFAULT 1,
    created_at INTEGER NOT NULL,
    UNIQUE (sheet_id, stable_key)
);

CREATE TABLE IF NOT EXISTS sum_rows (
    id TEXT PRIMARY KEY,
    sheet_id TEXT NOT NULL,
    stable_key TEXT NOT NULL,
    position INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL,
    deleted_at INTEGER,
    UNIQUE (sheet_id, stable_key)
);

CREATE TABLE IF NOT EXISTS sum_cells (
    sheet_id TEXT NOT NULL,
    row_index INTEGER NOT NULL,
    column_index INTEGER NOT NULL,
    value_type TEXT NOT NULL,
    value_json TEXT,
    formula TEXT,
    style_json TEXT,
    revision INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY (sheet_id, row_index, column_index)
);
CREATE INDEX IF NOT EXISTS idx_cells_viewport ON sum_cells(sheet_id, row_index, column_index);
CREATE INDEX IF NOT EXISTS idx_cells_column ON sum_cells(sheet_id, column_index, row_index);

CREATE TABLE IF NOT EXISTS sum_column_segments (
    sheet_id TEXT NOT NULL,
    column_id TEXT NOT NULL,
    segment_id TEXT NOT NULL,
    row_start INTEGER NOT NULL,
    row_end INTEGER NOT NULL,
    encoding TEXT NOT NULL,
    compressed_values BLOB,
    statistics_json TEXT,
    PRIMARY KEY (sheet_id, column_id, segment_id)
);

CREATE TABLE IF NOT EXISTS sum_operations (
    operation_id TEXT PRIMARY KEY,
    actor_id INTEGER NOT NULL,
    base_revision INTEGER NOT NULL,
    result_revision INTEGER NOT NULL,
    operation_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_file_operations_revision ON sum_operations(result_revision);

CREATE TABLE IF NOT EXISTS sum_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    revision INTEGER NOT NULL,
    snapshot_json TEXT NOT NULL,
    checksum TEXT,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS sum_statistics (
    sheet_id TEXT NOT NULL,
    column_key TEXT NOT NULL,
    filter_count INTEGER NOT NULL DEFAULT 0,
    sort_count INTEGER NOT NULL DEFAULT 0,
    access_count INTEGER NOT NULL DEFAULT 0,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY (sheet_id, column_key)
);

CREATE TABLE IF NOT EXISTS sum_indexes (
    id TEXT PRIMARY KEY,
    sheet_id TEXT NOT NULL,
    column_key TEXT NOT NULL,
    index_type TEXT NOT NULL DEFAULT 'sorted',
    state TEXT NOT NULL DEFAULT 'pending',
    statistics_json TEXT,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS sum_index_entries (
    index_id TEXT NOT NULL,
    value_json TEXT NOT NULL,
    row_index INTEGER NOT NULL,
    PRIMARY KEY (index_id, value_json, row_index)
);
CREATE INDEX IF NOT EXISTS idx_file_index_lookup ON sum_index_entries(index_id, value_json);

-- Data validation rules applied to ranges of a sheet.
CREATE TABLE IF NOT EXISTS sum_validations (
    id TEXT PRIMARY KEY,
    sheet_id TEXT NOT NULL,
    row_start INTEGER NOT NULL,
    row_end INTEGER NOT NULL,
    column_start INTEGER NOT NULL,
    column_end INTEGER NOT NULL,
    rule_type TEXT NOT NULL,
    criteria_json TEXT NOT NULL,
    allow_blank INTEGER NOT NULL DEFAULT 1,
    message TEXT,
    created_by INTEGER NOT NULL,
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_file_validations_sheet ON sum_validations(sheet_id);

-- Conditional formatting rules: when criteria match, apply style_json.
CREATE TABLE IF NOT EXISTS sum_conditional_formats (
    id TEXT PRIMARY KEY,
    sheet_id TEXT NOT NULL,
    row_start INTEGER NOT NULL,
    row_end INTEGER NOT NULL,
    column_start INTEGER NOT NULL,
    column_end INTEGER NOT NULL,
    rule_type TEXT NOT NULL,
    criteria_json TEXT NOT NULL,
    style_json TEXT NOT NULL,
    created_by INTEGER NOT NULL,
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_file_cformats_sheet ON sum_conditional_formats(sheet_id);

-- Cell comments (thread-lite: a comment per cell, resolvable).
CREATE TABLE IF NOT EXISTS sum_comments (
    id TEXT PRIMARY KEY,
    sheet_id TEXT NOT NULL,
    row_index INTEGER NOT NULL,
    column_index INTEGER NOT NULL,
    author_id INTEGER NOT NULL,
    body TEXT NOT NULL,
    resolved INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_file_comments_cell ON sum_comments(sheet_id, row_index, column_index);

-- Persisted per-sheet freeze-pane state (rows/columns frozen from 0).
CREATE TABLE IF NOT EXISTS sum_freeze (
    sheet_id TEXT PRIMARY KEY,
    freeze_rows INTEGER NOT NULL DEFAULT 0,
    freeze_columns INTEGER NOT NULL DEFAULT 0
);
"""


def upgrade_workbook_schema(connection, format_version: str) -> None:
    """Apply the supported format-1 additive schema contract.

    Version 1 is not a general migration engine: it can create missing tables
    and indexes idempotently, but it does not rewrite incompatible existing
    tables or infer altered columns. A future on-disk format must add an
    explicit version branch here before it is marked supported; unknown or
    missing versions fail closed and are never rewritten.
    """
    if str(format_version) != "1":
        raise sqlite3.DatabaseError(
            f"Unsupported PlexiSum format version: {format_version}"
        )
    connection.executescript(WORKBOOK_FILE_SCHEMA)


def ensure_feature_tables(connection) -> None:
    """Validate and apply additive setup for a supported ``.plexisum`` file.

    This is deliberately fail-closed for missing/unknown markers and
    incompatible columns. It is format validation plus idempotent table/index
    creation, not a promise to migrate arbitrary legacy SQLite files.
    """
    format_row = connection.execute(
        "SELECT value FROM sum_meta WHERE key = 'format_version'"
    ).fetchone()
    if format_row is None:
        raise sqlite3.DatabaseError("Missing PlexiSum format version")
    format_version = str(format_row[1])
    if format_version not in SUPPORTED_PLEXISUM_FORMAT_VERSIONS:
        raise sqlite3.DatabaseError(
            f"Unsupported PlexiSum format version: {format_version}"
        )
    upgrade_workbook_schema(connection, format_version)
    for table_name, required_columns in WORKBOOK_SCHEMA_REQUIREMENTS.items():
        columns = {
            row[1]
            for row in connection.execute(f"PRAGMA table_info({table_name})").fetchall()
        }
        if not required_columns.issubset(columns):
            missing = sorted(required_columns - columns)
            raise sqlite3.DatabaseError(
                f"Unsupported PlexiSum schema for {table_name}; missing columns: {missing}"
            )


def create_workbook_file(path: str, workbook_id: Any = None) -> None:
    """Create a fresh empty ``.plexisum`` workbook file at ``path``.

    The file is an independent SQLite database with the stable physical tables
    and the dynamic logical schema hooks described above. It is stored through
    the main file-storage adapter; it is never a main-database table. Existing
    files are accepted only when their format marker and required columns match
    the supported contract.
    """
    import sqlite3

    connection = sqlite3.connect(str(path))
    try:
        connection.executescript(WORKBOOK_FILE_SCHEMA)
        connection.execute(
            "INSERT OR REPLACE INTO sum_meta (key, value) VALUES (?, ?)",
            ("format_version", PLEXISUM_FORMAT_VERSION),
        )
        if workbook_id is not None:
            connection.execute(
                "INSERT OR REPLACE INTO sum_meta (key, value) VALUES (?, ?)",
                ("workbook_id", str(workbook_id)),
            )
        connection.execute(
            "INSERT OR REPLACE INTO sum_meta (key, value) VALUES (?, ?)",
            ("created_at", str(int(__import__("time").time() * 1000))),
        )
        connection.commit()
    finally:
        connection.close()


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
