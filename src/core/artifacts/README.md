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
- `capabilities.py` — the **capability / availability service** (see
  [Capability service](#capability-service) below). It evaluates the runtime
  availability of every artifacts feature and is exposed via API endpoints.

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

## Capability service

`capabilities.py` computes the **availability state** of each artifacts feature
so the admin panel (and clients) can show banners explaining why a feature is
unavailable.

### `CapabilityState` enum

Each feature resolves to exactly one state:

| State                  | Meaning |
|------------------------|---------|
| `available`            | The feature is fully usable. |
| `disabled_by_config`   | Turned off via server config. |
| `disabled_by_license`  | Off because the required license feature is absent. |
| `dependency_missing`   | A runtime dependency (e.g. Whisper) is not installed. |
| `misconfigured`        | Enabled and licensed, but required configuration (e.g. an API key) is missing, or the provider is unknown. |

`CapabilityInfo` carries `feature`, `state`, `message` (human-readable,
admin-facing), and `details` (optional extras such as the selected provider).

### Evaluated features

`get_artifact_capabilities(config=None)` returns a dict keyed by feature name:

- **`artifacts`** — master switch; `disabled_by_config` when
  `artifacts.enabled is False`, else `available`.
- **`artifacts_editor`** — `disabled_by_config` when
  `artifacts.editor.enabled is False` or `artifacts.enabled is False`.
- **`artifacts_whiteboard`** — `disabled_by_config` when
  `artifacts.whiteboard.enabled is False` or `artifacts.enabled is False`;
  `disabled_by_license` when `has_feature("artifacts_whiteboard")` is false;
  else `available`.
- **`voice_transcription`** — `disabled_by_config` when
  `artifacts.voice.transcription.enabled is False` or `artifacts.enabled is
  False`; `disabled_by_license` when `has_feature("voice_transcription")` is
  false; otherwise evaluated by provider:
  - `local_whisper` → `dependency_missing` if Whisper is not importable, else
    `available`.
  - `openai` / `azure` → `misconfigured` if the corresponding API key is empty,
    else `available`.
  - unknown provider → `misconfigured`.
- **`voice_recording`** — `disabled_by_config` when
  `artifacts.voice.allow_recording is False`, else `available`.

Both functions accept an optional `config` dict; when omitted the artifacts
config is loaded through the standard config accessor. Neither function ever
raises. `get_capability(feature, config=None)` returns the info for one feature.

### API endpoints

- `GET /api/v1/capabilities` (auth required, user scope) — returns the per-feature
  capability dict (`{feature: {state, message, details}}`) for client notices.
  Defined in `src/api/routes/capabilities.py`.
- `GET /api/v1/admin/capabilities` (admin guarded) — returns the same
  per-feature breakdown plus a top-level `summary` (counts and a `by_state`
  grouping). Defined in `src/api/routes/admin/capabilities.py`.
