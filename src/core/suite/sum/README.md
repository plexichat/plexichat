# PlexiSum

PlexiSum is the workbook and high-performance data-layer for Plexichat. Each workbook is an independent SQLite database (`.plexisum`) stored through the main file-storage adapter (S3 or local filesystem). The main database holds only metadata, ownership, and ACL — never workbook cells.

## Overview

PlexiSum provides a spreadsheet-like engine with:

- **File-backed isolation**: Every workbook is a standalone `.plexisum` file with its own SQLite schema.
- **Optimistic concurrency**: Readers get snapshot isolation; writers use compare-and-swap on a revision counter.
- **Coalesced writes**: Cell edits are buffered in a durable operation log and flushed to immutable blobs on a configurable window.
- **Formula evaluation**: A bounded formula engine with cycle detection, cross-sheet references, and lazy evaluation.
- **Column segments**: Dense packed storage with auto-selected encoding (run-length, delta, dictionary) for viewport reads.
- **Manifest integrity**: The container format uses SHA-256 checksums and zstandard compression.

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│  SumManager (facade)                                    │
│  ┌───────────┬───────────┬──────────┬──────────────┐    │
│  │ Workbooks │   Data    │  Admin   │  Operations  │    │
│  ├───────────┼───────────┼──────────┼──────────────┤    │
│  │ Sessions  │ Snapshots │ Features │   Viewport   │    │
│  ├───────────┼───────────┼──────────┼──────────────┤    │
│  │ Exchange  │ Revisions │          │              │    │
│  └───────────┴───────────┴──────────┴──────────────┘    │
│                      │                                   │
│  ┌───────────────────┴──────────────────────────────┐   │
│  │  BlobStore (MediaBlobStore / LocalBlobStore)     │   │
│  └──────────────────────────────────────────────────┘   │
│                      │                                   │
│  ┌───────────────────┴──────────────────────────────┐   │
│  │  .plexisum file (container format)                │   │
│  │  ┌──────────────────────────────────────────┐    │   │
│  │  │  SQLite DB (cells, sheets, operations)    │    │   │
│  │  └──────────────────────────────────────────┘    │   │
│  └──────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────┘
```

### Main database tables

| Table | Purpose |
|---|---|
| `sum_workbooks` | Workbook identity, ownership, revision counter, blob pointers |
| `sum_grants` | Per-workbook ACL (subject, permissions, expiry) |
| `sum_range_permissions` | Per-range read/edit restrictions |
| `sum_snapshots` | Named snapshot pointers |
| `sum_operations` | Durable operation buffer between flushes |
| `sum_features` | Client-authored visualization definitions |
| `sum_revision_blobs` | Immutable revision blob metadata |

### Workbook file tables

| Table | Purpose |
|---|---|
| `sum_meta` | Format version marker |
| `sum_sheets` | Sheet definitions |
| `sum_columns` | Column metadata (types, display names, positions) |
| `sum_rows` | Row metadata |
| `sum_cells` | Typed, sparse cell storage |
| `sum_column_segments` | Packed column segments for viewport reads |
| `sum_statistics` | Access/filter/sort counters for index promotion |

## Key files

| File | Purpose |
|---|---|
| `__init__.py` | Module facade — `setup()`, `get_manager()`, re-exports |
| `manager.py` | `SumManager` class (mixin composition of all manager modules) |
| `manager_base.py` | Base manager shared logic |
| `manager_workbooks.py` | Workbook CRUD operations |
| `manager_data.py` | Cell read/write operations |
| `manager_operations.py` | Operation log and flush logic |
| `manager_sessions.py` | Coalesced write sessions |
| `manager_snapshots.py` | Snapshot creation and restore |
| `manager_viewport.py` | Viewport reads (cell ranges for rendering) |
| `manager_features.py` | Visualization definitions (charts, pivots) |
| `manager_exchange.py` | Import/export (CSV) |
| `manager_admin.py` | Administrative operations |
| `manager_revisions.py` | Revision history and GC |
| `schema.py` | DDL for main DB and workbook file schemas |
| `container.py` | `.plexisum` container format (header + zstd + SHA-256) |
| `segments.py` | Column segment encoding (plain, run-length, delta, dictionary) |
| `formulas.py` | Bounded formula evaluator facade |
| `_formula_core.py` | Formula tokenizer, parser, core evaluator |
| `_formula_advanced.py` | Advanced formula functions |
| `_formula_shared.py` | Shared formula constants, error codes, cell resolver protocol |
| `_formula_protocol.py` | Formula protocol definitions |
| `_shared.py` | Constants, errors, blob stores, session state |

## Usage

### Initialization

```python
from src.core.suite.sum import setup, get_manager

# Initialize with the main database, media adapter, and storage directory
manager = setup(
    db=main_db,
    servers_module=servers,
    media=media_adapter,
    storage_dir="/data/workbooks",
)
```

### Create a workbook

```python
manager = get_manager()
workbook = manager.create_workbook(
    title="Sales Q1",
    owner_id=user_id,
    scope="personal",
)
```

### Read cells

```python
cells = manager.get_viewport(
    workbook_id=workbook["id"],
    sheet_id="sheet-1",
    row_start=0,
    row_end=100,
    col_start=0,
    col_end=26,
)
```

### Write cells

```python
manager.apply_operation(
    workbook_id=workbook["id"],
    actor_id=user_id,
    operation_type="set_cell",
    payload={
        "sheet_id": "sheet-1",
        "row": 0,
        "column": 0,
        "value_type": "text",
        "value": "Hello",
    },
)
```

### Evaluate formulas

```python
from src.core.suite.sum.formulas import compute_value

result = compute_value(
    value_type="formula",
    value=None,
    formula="SUM(A1:A100)",
    resolver=cell_resolver,
    sheet="sheet-1",
)
```

## Configuration

Key constants in `_shared.py`:

| Constant | Default | Purpose |
|---|---|---|
| `MAX_SNAPSHOTS_KEPT` | 50 | Snapshots garbage-collected beyond this count |
| `REVISION_KEEP_COUNT` | 5 | Immutable revision blobs retained per workbook |
| `WORKBOOK_CACHE_MAX_ENTRIES` | 32 | Max cached workbook files per process |
| `WORKBOOK_CACHE_MAX_BYTES` | 256 MB | Max memory for cached workbook files |
| `SUM_SESSION_MAX_ENTRIES` | 32 | Live in-memory workbook copies per process |
| `SUM_FLUSH_MAX_OPS` | 25 | Unflushed ops before forced flush |
| `SUM_FLUSH_MAX_MS` | 2000 | Age (ms) of oldest unflushed op before flush |
| `MAX_CSV_ROWS` | 10,000 | Max rows for CSV import |
| `MAX_CSV_COLUMNS` | 100 | Max columns for CSV import |
| `PRESENCE_TTL_SECONDS` | 30 | Presence heartbeat expiry |

## Container format

The `.plexisum` binary container (`container.py:1-184`):

```
Header (16 bytes):
  magic:           b"PXSM"       (4 bytes)
  format_version:  u8 = 1        (1 byte)
  compression:     u8 (0/1)      (1 byte)
  flags:           u8            (1 byte)
  reserved:        zeroed        (9 bytes)

Body:  zstd-compressed (or raw) SQLite payload
Footer (32 bytes): SHA-256 checksum of uncompressed payload
```

## Related code

- `src/core/suite/sum/__init__.py:25` — `setup()` entry point
- `src/core/suite/sum/schema.py:37` — Main database DDL
- `src/core/suite/sum/schema.py:370` — Workbook file DDL
- `src/core/suite/sum/container.py:91` — `encode_container()` / `decode_container()`
- `src/core/suite/sum/segments.py:241` — `SegmentEncoder.encode_segment()`
- `src/core/suite/sum/formulas.py:48` — `compute_value()` top-level entry
- `src/core/suite/sum/manager.py:73` — `SumManager` class definition
- `src/core/suite/sum/_shared.py:53` — `_BoundedFileCache` implementation
