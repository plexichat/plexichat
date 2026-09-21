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

## Formula semantics

This engine and the PlexiSum client engine (`plexichat-client`, see its
`formula-engine/README.md`) implement the same formula language and are held to
the same answers. `src/tests/suite/formula_parity_cases.json` is the
machine-checked statement of that: every case is evaluated against both engines
from the identical file, so a semantic change in one engine and not the other
fails a test instead of drifting silently until someone notices a different
answer on screen. The client repository carries a byte-identical copy of the
same file.

Two rules explain most of the behaviour:

**An error operand propagates.** Pass an error into a function and it comes
back out: `SUM(1, 1/0)`, `PROPER(1/0)`, `ROUND(1, 1/0)`, `DATE(1/0, 1, 1)` and
`SUMIF(A1:A3, 1/0)` are all `#DIV/0!`. Three groups classify an error instead,
because they answer a question *about* the value rather than computing with it:

- `COUNT` counts numbers only, so an error is simply not counted;
- `COUNTA` counts anything non-empty, and an error is data;
- `COUNTBLANK` counts an error cell as non-blank;
- the `IS*` family and `TYPE` report `FALSE` and `16`.

**A text argument is coerced, a referenced value is not.** Excel translates a
text argument into a number (`SUM("2")` is 2) but never a value read from a
cell or range (`SUM(A1)` is 0 when A1 holds the text `"2"`), which is why a
text-formatted number column does not silently add up. `AVERAGE`, `MIN`, `MAX`,
`PRODUCT` and `COUNT` follow the same rule, and the whole argument has to read
as a number rather than a prefix of one (`SUM("2abc")` is 0).

**Criteria rank types.** `SUMIF`, `COUNTIF`, `AVERAGEIF` and their
multi-criteria forms compare each cell against the criteria through Excel's
sort order — number, then text, then `FALSE`, then `TRUE` — with text compared
case-insensitively, and the operators `<`, `<=`, `>`, `>=`, `<>` and `=` all
work on text as well as numbers. So `COUNTIF(range, "<>2")` counts the text
cells and `COUNTIF(range, ">2")` ignores them. A single cell is a 1×1 range to
these functions (`COUNTIF(A1, ">=1")`). A row that matches the criteria but
holds no number in the summed range contributes 0 to `SUMIF`/`SUMIFS`, while
`AVERAGEIF`/`AVERAGEIFS` skip it entirely — Excel ignores text and blanks in an
average range, so a match with no number at all is `#DIV/0!`.

### Known divergences from Excel

Deliberate, and shared with the client engine unless noted:

| Case | Excel | Here |
|---|---|---|
| `"a" < "b"` | `TRUE` | `#VALUE!` — comparisons coerce to a number |
| `SUM(TRUE, 1)` | `2` | `1` — logical *arguments* are not coerced |
| `COUNTA("")` | `1` | `0` — an empty-text argument counts as empty |
| `IF("x", 1, 2)` | `#VALUE!` | `1` — non-boolean text is truthy |
| `"" + 1` | `1` | `#VALUE!` — empty text does not coerce to 0 |
| `COUNTIF(A1:A3, "tw*")` | wildcard match | `0` — criteria wildcards are not implemented |
| `DATE`/`YEAR`/`MONTH`/`DAY` before 1900-03-01 | Excel's serials | one day lower (see `_DATE_EPOCH`; modern serials match) |
| `ISEVEN`, `ISREF`, `T`, `CLEAN`, most statistical/financial/engineering functions | implemented | `#NAME?` — the server engine is a deliberately compact subset |

### Functions the client engine has that this one does not

This engine implements 84 functions (`TRUE`/`FALSE` are literals, not
functions, so `TRUE()` is `#VALUE!`); the client engine implements 249. The gap
is by design — a viewport read, a CSV export and a sort need a compact,
cheap-to-verify subset — but it has never been written down, so it is listed
here, with what to adopt next and what to leave alone. Every function named
below is callable in the client engine today.

**Adopt next.** Cheap to implement on the machinery that is already here, and
common enough in real sheets to be worth the surface area:

| Family | Functions | Note |
|---|---|---|
| Lookup | `CHOOSE`, `IFS`, `SWITCH` | the `IF`/`IFNA` short-circuit path already exists |
| Information | `ISLOGICAL`, `ISNONTEXT`, `ISEVEN`, `ISODD`, `ERROR.TYPE` | `_information` and the `ERROR_CODES` table are already here; `N`, `T`, `CLEAN` are one-liners on `_to_number`/`_to_text` |
| Math | `SIGN`, `GCD`, `LCM`, `SUMSQ`, `SUMX2MY2`, `SUMX2PY2`, `SUMXY` | pure arithmetic over the existing aggregate walk |
| Trig | `ATAN2`, `SINH`, `COSH`, `TANH` | `math.atan2`/`math.sinh`… |
| Date | `HOUR`, `MINUTE`, `SECOND`, `TIME`, `DATEVALUE`, `TODAY`, `NOW` | the serial-number helpers already exist for `DATE`/`DAYS` |
| Statistics | `MEDIAN`, `LARGE`, `SMALL`, `RANK`, `STDEV`, `VAR`, `MODE`, `PERCENTILE`, `QUARTILE`, `AVERAGEA`, `MAXA`, `MINA` | sort/sum over the numbers the aggregates already collect |

**Adopt when a feature asks for it.** The rest of the statistical
`*DIST`/`*INV` family, `FORECAST`/`SLOPE`/`INTERCEPT`/`LINEST`/`TREND`, the
financial set (`NPV`, `IRR`, `PMT`, `FV`, …), `XLOOKUP`, and the dynamic-array
family (`SORT`, `FILTER`, `HSTACK`, `TAKE`, `WRAPROWS`, …). Each needs design
decisions this engine has not made yet — array results, iteration, numeric
methods — so they should arrive with the feature that wants them rather than as
speculative surface.

**Leave out.** Engineering conversions and complex arithmetic (`BIN2HEX`,
`IMSUM`, `CONVERT`, `BESSELJ`, …) are a large surface with narrow use, and the
client engine covers them where a formula needs one.

One inconsistency worth knowing: the client registers `TRUE` and `FALSE` as
functions (`TRUE()`), which Excel also accepts; here they are literals only.

The lexer accepts a name that is shaped like a cell reference (`ATAN2`, which is
both) and resolves it as a call when a parenthesis follows, and it accepts a dot
in a name, so adopting a name from the list above does not need a lexer change
first.

The engine is also bounded where Excel is not: `REPT`, `NETWORKDAYS` and
`WORKDAY` refuse to materialise a huge string or iterate for hours
(`MAX_REPT_CHARS`, `MAX_NETWORKDAYS_SPAN_DAYS`, `MAX_WORKDAY_OFFSET_DAYS`), and
return `#VALUE!`/`#NUM!` so a single formula cannot stall a viewport read.
A multi-cell `OFFSET` is one reference in Excel; here it is a flat value list
(the client engine represents it as a row-major matrix). `INDEX`'s "whole
column"/"whole row" forms (`INDEX(A1:C3, 0, 2)`) arrive the same way, so the
two shapes still add up identically under `SUM`.

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
