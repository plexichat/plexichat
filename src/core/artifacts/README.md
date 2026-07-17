# Artifacts Module

The artifacts module owns the database schema for Plexichat's **Artifacts**
feature: first-class, persistent records that represent durable outputs of a
conversation such as voice calls, whiteboards, uploads, files, transcripts,
and future artifact types.

At this stage the module contains **only the database schema**. Models,
managers, routes, and business logic are introduced by later groups and will
build on top of the tables defined here.

## Contents

- `schema.py` — exposes `create_tables(db)` which creates the three tables
  described below. It follows the same execution style as the voice and
  messaging modules: the schema SQL is split with
  `src.core.database.core.schema_splitter.split_sql_statements`, each
  statement is passed through `db.convert_schema(...)` when available, and
  executed via `db.execute(...)`. All statements use `CREATE TABLE IF NOT
  EXISTS` / `CREATE INDEX IF NOT EXISTS` so creation is idempotent.
- `__init__.py` — package marker and module docstring.

The tables are created by migration
`src/core/migrations/migrations/047_add_artifacts_tables.py`, which imports
`create_tables` from `schema.py` in its `up(db)` and drops the tables in its
`down(db)`.

## ID convention

All identifiers use the Snowflake format and are stored as `INTEGER`, matching
the convention used by the voice and messaging modules. The one exception is
`artifact_ops.id`, which is an auto-incrementing local row id
(`INTEGER PRIMARY KEY AUTOINCREMENT`); artifact ordering is expressed via the
`seq` column rather than the primary key.

## Tables

### `artifacts`

The central record for every artifact.

| Column            | Type    | Notes |
|-------------------|---------|-------|
| `id`              | INTEGER | Snowflake primary key. |
| `conversation_id` | INTEGER | Nullable; owning conversation (some future artifact types are not tied to a conversation). |
| `channel_id`      | INTEGER | Nullable; owning channel. |
| `server_id`       | INTEGER | Nullable; owning server. |
| `author_id`       | INTEGER | NOT NULL; user who created the artifact. |
| `artifact_type`   | TEXT    | NOT NULL; one of `voice_call`, `whiteboard`, `upload`, `file`, `transcript`, `future`. |
| `title`           | TEXT    | NOT NULL; display title. |
| `summary`         | TEXT    | Nullable; short summary/description. |
| `status`          | TEXT    | NOT NULL, default `completed`; one of `live`, `completed`, `archived`. |
| `recorded`        | INTEGER | NOT NULL, default `0`; boolean (0/1) indicating the artifact was recorded. |
| `has_transcript`  | INTEGER | NOT NULL, default `0`; boolean (0/1) indicating a transcript exists. |
| `payload`         | TEXT    | Nullable; JSON-encoded, type-specific data stored as text. |
| `retention_policy`| TEXT    | Nullable; per-artifact retention override (JSON or a days string). |
| `expires_at`      | INTEGER | Nullable; expiry timestamp. `NULL` means never expire. |
| `license_feature` | TEXT    | Nullable; license feature flag gating the artifact, e.g. `artifacts_whiteboard`. |
| `created_at`      | INTEGER | NOT NULL; creation timestamp. |
| `updated_at`      | INTEGER | NOT NULL; last-update timestamp. |

Indexes: `conversation_id`, `server_id`, `author_id`, `artifact_type`,
`created_at`.

### `voice_calls`

Call-specific metadata. A completed call typically produces an `artifacts`
row of type `voice_call`; `artifact_id` links back to it.

| Column                   | Type    | Notes |
|--------------------------|---------|-------|
| `id`                     | INTEGER | Snowflake primary key. |
| `artifact_id`            | INTEGER | Nullable; references `artifacts.id`. |
| `conversation_id`        | INTEGER | Nullable; owning conversation. |
| `channel_id`             | INTEGER | Nullable; owning channel. |
| `server_id`              | INTEGER | Nullable; owning server. |
| `initiator_id`          | INTEGER | User who started the call. |
| `started_at`             | INTEGER | NOT NULL; call start timestamp. |
| `ended_at`               | INTEGER | Nullable; call end timestamp. |
| `duration_seconds`       | INTEGER | Nullable; total call duration. |
| `recorded`               | INTEGER | NOT NULL, default `0`; boolean (0/1). |
| `transcript_artifact_id` | INTEGER | Nullable; artifact id of the transcript, if any. |
| `consented_participants` | TEXT    | Nullable; JSON list of user ids that consented to recording. |
| `participant_count`      | INTEGER | Default `0`; number of participants. |
| `created_at`             | INTEGER | NOT NULL. |
| `updated_at`             | INTEGER | NOT NULL. |

Indexes: `artifact_id`, `conversation_id`, `server_id`.

### `artifact_ops`

An ordered, append-only operations log for collaborative artifacts (for
example whiteboard strokes and edits). Operations are ordered per artifact by
`seq`.

| Column       | Type    | Notes |
|--------------|---------|-------|
| `id`         | INTEGER | Auto-incrementing local row id (`PRIMARY KEY AUTOINCREMENT`). |
| `artifact_id`| INTEGER | NOT NULL; the artifact this op belongs to. |
| `seq`        | INTEGER | NOT NULL; monotonic per-artifact sequence number. |
| `op_type`    | TEXT    | NOT NULL; e.g. `stroke`, `edit`, `cursor`, `snapshot`. |
| `actor_id`   | INTEGER | Nullable; user who produced the op. |
| `data`       | TEXT    | NOT NULL; JSON-encoded op payload. |
| `created_at` | INTEGER | NOT NULL. |

Constraints: `UNIQUE(artifact_id, seq)`.
Indexes: `artifact_id`, and the composite `(artifact_id, seq)`.

## How later groups use this module

Later groups will add:

- Snowflake-backed domain models mapping to these tables.
- A manager/service layer for creating and querying artifacts, recording voice
  calls, and appending/replaying `artifact_ops`.
- API routes and real-time delivery.

Those groups should depend on the tables and columns documented here rather
than redefining schema.
