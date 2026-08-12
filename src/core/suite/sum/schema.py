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
from typing import Any

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
    -- Revision the stored blob_path is materialized at. When it lags
    -- current_revision the gap is covered by the durable sum_operations log
    -- and replayed on materialization; coalesced cell writes advance
    -- current_revision per op but only promote a new blob on flush.
    blob_revision BIGINT NOT NULL DEFAULT 0,
    blob_path TEXT,
    file_size BIGINT NOT NULL DEFAULT 0,
    checksum TEXT,
    deleted INTEGER NOT NULL DEFAULT 0,
    -- Home-surface metadata. Pinned workbooks float to the top of the owner's
    -- list; templates are flagged workbooks a user can instantiate copies
    -- from. index_state / calculation_state are refreshed whenever the file
    -- is materialized (see SumManager._refresh_workbook_status).
    pinned INTEGER NOT NULL DEFAULT 0,
    is_template INTEGER NOT NULL DEFAULT 0,
    index_state TEXT NOT NULL DEFAULT 'none',
    calculation_state TEXT NOT NULL DEFAULT 'none',
    created_at BIGINT NOT NULL,
    updated_at BIGINT NOT NULL,
    CHECK (scope IN ('personal', 'server')),
    CHECK ((scope = 'personal' AND server_id IS NULL) OR (scope = 'server' AND server_id IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS idx_sum_workbooks_owner ON sum_workbooks(owner_id, deleted, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_sum_workbooks_server ON sum_workbooks(server_id, deleted, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_sum_workbooks_artifact ON sum_workbooks(artifact_id);

-- Immutable workbook revision blobs are storage metadata only. Cell data stays
-- exclusively in the independent SQLite file referenced by blob_path.
CREATE TABLE IF NOT EXISTS sum_revision_blobs (
    id BIGINT PRIMARY KEY,
    workbook_id BIGINT NOT NULL,
    revision BIGINT NOT NULL,
    blob_path TEXT NOT NULL UNIQUE,
    checksum TEXT NOT NULL,
    created_at BIGINT NOT NULL,
    FOREIGN KEY (workbook_id) REFERENCES sum_workbooks(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_sum_revision_blobs_workbook
    ON sum_revision_blobs(workbook_id, revision DESC, created_at DESC);

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

-- Durable operation buffer between flushed snapshots. Every cell operation is
-- appended here (with the revision CAS) before it is acknowledged, so a crash
-- between flushes loses nothing; materialization replays rows whose
-- result_revision exceeds the stored blob_revision. Rows up to the flushed
-- revision are removed during each flush or full-file rewrite, keeping the
-- buffer bounded by the coalescing window. Workbook cells remain exclusively
-- in .plexisum files; this table never carries cell data by itself.
CREATE TABLE IF NOT EXISTS sum_operations (
    id BIGINT PRIMARY KEY,
    workbook_id BIGINT NOT NULL,
    operation_id TEXT NOT NULL,
    actor_id BIGINT NOT NULL,
    base_revision BIGINT NOT NULL,
    result_revision BIGINT NOT NULL,
    operation_type TEXT NOT NULL,
    sheet_id TEXT,
    payload_json TEXT NOT NULL,
    created_at BIGINT NOT NULL,
    FOREIGN KEY (workbook_id) REFERENCES sum_workbooks(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_sum_operations_workbook_revision
    ON sum_operations(workbook_id, result_revision);
CREATE UNIQUE INDEX IF NOT EXISTS idx_sum_operations_workbook_operation
    ON sum_operations(workbook_id, operation_id);
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


def _main_table_columns(db, table_name: str) -> set[str]:
    """Return columns for a known Sum metadata table across DB backends."""
    if getattr(db, "type", "sqlite") == "sqlite":
        rows = db.fetch_all(f"PRAGMA table_info({table_name})")
        return {str(row.get("name")) for row in rows}
    rows = db.fetch_all(
        "SELECT column_name AS name FROM information_schema.columns "
        "WHERE table_schema = current_schema() AND table_name = ?",
        (table_name,),
    )
    return {str(row.get("name")) for row in rows}


def _ensure_main_columns(db) -> None:
    """Apply additive metadata changes for databases created by older builds.

    ``CREATE TABLE IF NOT EXISTS`` does not add columns to an existing table.
    The initial migration remains the canonical fresh-install schema, while
    this small compatibility step keeps already-initialized databases usable
    without putting workbook cells in the main database.
    """
    columns = _main_table_columns(db, "sum_workbooks")
    if "artifact_id" not in columns:
        _add_main_column(db, "sum_workbooks", "artifact_id", "BIGINT")
    # Existing databases predating the coalesced-write buffer lack
    # blob_revision; seed it to current_revision so the stored blob is treated
    # as authoritative (the log is empty for those workbooks, so nothing is
    # lost). New installs get the CREATE TABLE default of 0.
    if "blob_revision" not in columns:
        _add_main_column(
            db, "sum_workbooks", "blob_revision", "BIGINT NOT NULL DEFAULT 0"
        )
        db.execute(
            "UPDATE sum_workbooks SET blob_revision = current_revision "
            "WHERE blob_revision = 0 AND current_revision > 0"
        )
    # Home-surface metadata columns (additive; older databases get them via
    # ALTER TABLE so boot-time setup never rewrites the table).
    if "pinned" not in columns:
        _add_main_column(db, "sum_workbooks", "pinned", "INTEGER NOT NULL DEFAULT 0")
    if "is_template" not in columns:
        _add_main_column(
            db, "sum_workbooks", "is_template", "INTEGER NOT NULL DEFAULT 0"
        )
    if "index_state" not in columns:
        _add_main_column(
            db, "sum_workbooks", "index_state", "TEXT NOT NULL DEFAULT 'none'"
        )
    if "calculation_state" not in columns:
        _add_main_column(
            db, "sum_workbooks", "calculation_state", "TEXT NOT NULL DEFAULT 'none'"
        )


def _add_main_column(db, table_name: str, column: str, definition: str) -> None:
    """Add one metadata column to a Sum main-database table."""
    statement = f"ALTER TABLE {table_name} ADD COLUMN {column} {definition}"
    if getattr(db, "type", "sqlite") != "sqlite":
        statement = (
            f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS {column} {definition}"
        )
    db.execute(statement)


def create_tables(db) -> None:
    """Create Sum metadata tables and apply safe additive compatibility fixes."""
    statements = [s for s in split_sql_statements(MAIN_SCHEMA) if s.strip()]
    artifact_index = next(
        (
            statement
            for statement in statements
            if "CREATE INDEX IF NOT EXISTS idx_sum_workbooks_artifact" in statement
        ),
        None,
    )
    # An old sum_workbooks table may not have artifact_id yet, so defer that
    # index until the compatibility column has been added below.
    for statement in statements:
        if statement == artifact_index:
            continue
        converted = (
            db.convert_schema(statement) if hasattr(db, "convert_schema") else statement
        )
        db.execute(converted)
    _ensure_main_columns(db)
    if artifact_index is not None:
        converted = (
            db.convert_schema(artifact_index)
            if hasattr(db, "convert_schema")
            else artifact_index
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
    started_transaction = not connection.in_transaction
    savepoint_name = "plexisum_schema_upgrade"
    if started_transaction:
        connection.execute("BEGIN")
    else:
        connection.execute(f"SAVEPOINT {savepoint_name}")
    try:
        for statement in split_sql_statements(WORKBOOK_FILE_SCHEMA):
            if statement.strip():
                connection.execute(statement)
        if started_transaction:
            connection.commit()
        else:
            connection.execute(f"RELEASE SAVEPOINT {savepoint_name}")
    except Exception:
        if started_transaction:
            connection.rollback()
        else:
            connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint_name}")
            connection.execute(f"RELEASE SAVEPOINT {savepoint_name}")
        raise


def ensure_feature_tables(connection) -> None:
    """Validate and apply additive setup for a supported ``.plexisum`` file.

    This is deliberately fail-closed for missing/unknown markers and
    incompatible columns. It is format validation plus idempotent table/index
    creation, not a promise to migrate arbitrary legacy SQLite files.
    """
    meta_exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'sum_meta'"
    ).fetchone()
    if not meta_exists:
        raise sqlite3.DatabaseError("Missing PlexiSum metadata table")
    format_row = connection.execute(
        "SELECT value FROM sum_meta WHERE key = 'format_version'"
    ).fetchone()
    if format_row is None:
        raise sqlite3.DatabaseError("Missing PlexiSum format version")
    format_version = str(format_row[0])
    if format_version not in SUPPORTED_PLEXISUM_FORMAT_VERSIONS:
        raise sqlite3.DatabaseError(
            f"Unsupported PlexiSum format version: {format_version}"
        )
    # Validate all tables that already exist before applying any additive DDL.
    # Missing feature tables are the supported format-1 upgrade case; an
    # existing table with incompatible columns is rejected without mutating the
    # file first.
    for table_name, required_columns in WORKBOOK_SCHEMA_REQUIREMENTS.items():
        table_exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table_name,),
        ).fetchone()
        if not table_exists:
            continue
        columns = {
            row[1]
            for row in connection.execute(f"PRAGMA table_info({table_name})").fetchall()
        }
        if not required_columns.issubset(columns):
            missing = sorted(required_columns - columns)
            raise sqlite3.DatabaseError(
                f"Unsupported PlexiSum schema for {table_name}; missing columns: {missing}"
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

    The file on disk is a PlexiSum *container* (header + compressed body +
    checksum).  The body is a SQLite database carrying the stable physical
    tables and dynamic logical schema hooks described above.  It is stored
    through the main file-storage adapter; it is never a main-database table.
    Existing files are accepted only when their format marker and required
    columns match the supported contract.
    """
    import datetime
    import sqlite3

    from .container import encode_container

    connection = sqlite3.connect(":memory:")
    try:
        connection.execute("BEGIN")
        for statement in split_sql_statements(WORKBOOK_FILE_SCHEMA):
            if statement.strip():
                connection.execute(statement)
        connection.execute(
            "INSERT OR REPLACE INTO sum_meta (key, value) VALUES (?, ?)",
            ("format_version", PLEXISUM_FORMAT_VERSION),
        )
        connection.execute(
            "INSERT OR REPLACE INTO sum_meta (key, value) VALUES (?, ?)",
            ("container_version", "1"),
        )
        connection.execute(
            "INSERT OR REPLACE INTO sum_meta (key, value) VALUES (?, ?)",
            (
                "created_at_date",
                datetime.datetime.now(datetime.timezone.utc).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                ),
            ),
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
        raw_bytes = connection.serialize()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    container = encode_container(raw_bytes)
    with open(path, "wb") as fh:
        fh.write(container)
