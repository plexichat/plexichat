# PlexiSum data model and performance

## Product goal

PlexiSum is a collaborative, permission-aware spreadsheet and structured-data
app integrated with Plexichat. It should be optimized for targeted large-data
workloads, not marketed as universally faster than Excel before benchmarks
exist.

Its differentiators are indexed viewport loading, lazy data access, range
operations, collaboration, permissions, comments, mentions, and chat context.

## Three-layer model

### 1. Plexichat artifact identity

The `sum` artifact owns title, creator, scope, permissions, sharing, current
revision, capability, and the link to the workbook. It is not the complete
cell payload.

### 2. Sum data layer

The Sum repository owns workbook/sheet/row/column metadata, typed values,
formulas, indexes, snapshots, operations, and query statistics.

### 3. UI engine adapter

Univer or FortuneSheet owns selection, editing controls, formatting, menus,
keyboard behavior, and rendering. It is not authoritative persistence.

## Logical schema

```text
sum_workbooks
  workbook_id, artifact_id, title, current_revision,
  calculation_mode, index_state, calculation_state, timestamps

sum_sheets
  sheet_id, workbook_id, name, position, row_count, column_count,
  frozen_rows, frozen_columns, timestamps

sum_columns
  column_id, sheet_id, stable_key, display_name, data_type, position,
  width, format, nullable, default_value, formula_expression

sum_rows
  row_id, sheet_id, stable_key, position, sort_key, deleted_at, timestamps

sum_cells (typed sparse option)
  sheet_id, row_id, column_id, value_type, typed value fields,
  formula, style_id, updated_at

sum_column_segments (dense option)
  sheet_id, column_id, segment_id, row_start, row_end,
  encoding, compressed_values, statistics

sum_operations
  operation_id, workbook_id, actor_id, base_revision, result_revision,
  operation_type, bounded payload, created_at

sum_snapshots
  snapshot_id, workbook_id, revision, storage reference, checksum, created_at

sum_indexes
  workbook_id, sheet_id, column_id, index_type, state, statistics
```

This is a logical design, not a migration. Stable physical tables are preferred
over creating a physical SQL table from every user-defined column name.

## Dynamic schemas

A workbook still has a dynamic logical schema through `sum_columns`. Physical
DDL should remain stable because per-user table creation introduces lock,
quoting, migration, backup, and object-count problems. Managed typed indexes
may be created for measured filter/sort workloads, with safe identifier
mapping and lifecycle management.

Use a hybrid representation:

- typed sparse cells for irregular sheets
- column segments for dense/high-volume columns
- metadata and statistics to choose query paths
- optional indexes only where workload justifies write/storage cost

## SQLite and deployment

SQLite/WASM is a strong client-side cache/offline engine. SQLite is also a
reasonable single-node authoritative deployment. The backend domain contract
must remain database-agnostic so PostgreSQL can support larger multi-worker
installations.

Do not assume SQLite's single-writer model solves distributed collaboration.
Server-side sequencing, idempotency, permissions, snapshots, and worker-safe
coordination remain required.

## Viewport loading

The browser must never materialize millions of cells. Requests are bounded by
sheet, row range, column range, and known revision. The renderer receives the
visible range plus overscan:

```text
scroll → bounded viewport request → indexed query → visible cells only → render
```

Responses need size limits, cancellation/stale-request handling, partial loading
states, and revision metadata.

## Collaboration operations

Every durable operation includes:

- operation ID/idempotency key
- workbook ID
- actor ID
- base revision
- server result revision
- operation type
- bounded payload
- timestamp

Operations include set cell, set formula, compressed set range, insert/delete
row/column, sheet changes, and formatting. Large pastes must be range operations,
not one message per cell. Reconnect resumes after the last acknowledged
revision. Snapshots compact old operations.

## Calculation

Start with client-visible/simple formulas and server persistence. Expensive or
authoritative calculations need a dedicated calculation layer and dependency
tracking, not full-workbook recalculation on every WebSocket worker.

## Initial product scope

- workbooks and worksheets
- editing and formatting
- formulas
- row/column operations
- freeze panes
- sorting/filtering
- validation and conditional formatting
- revisions and snapshots
- comments/discussions
- Plexichat sharing, mentions, and notifications
- CSV import/export first
- XLSX after the core model is stable

## Engine evaluation

Prototype Univer OSS and FortuneSheet behind the same adapter. Compare actual
integration cost, viewport behavior, formula support, operation capture,
accessibility, bundle/memory cost, Vite integration, and license obligations
before forking either project.
