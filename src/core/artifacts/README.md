# Artifacts Module

The artifacts module owns the database schema for Plexichat's **Artifacts**
feature: first-class, persistent records that represent durable outputs of a
conversation such as voice calls, whiteboards, uploads, files, transcripts,
and future artifact types.

This stage adds the **domain logic** for artifacts (Group 4): dataclass models,
a repository (DB access), and a manager (business logic). No routes, no
websocket handlers, and no voice-call-specific logic are included here — those
are added by later groups.

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

## Voice call artifacts

When a voice call ends it produces a `voice_call` record plus a corresponding
`voice_call` Artifact, so the call appears in chat history and the artifacts
pane carrying recording/transcript flags and participant consent.

### Lifecycle

A "call" is an aggregate over a voice channel. It is created lazily, keyed by
`channel_id`:

- **Start** — when the first participant joins a channel (see
  `VoiceCallManager.start_call`), a `voice_calls` row is inserted
  (`started_at` now, `participant_count` 1) and a linked `voice_call` Artifact
  is created via `ArtifactManager.create(...)` with `artifact_type =
  ArtifactType.VOICE_CALL`, `status = ArtifactStatus.LIVE`, `recorded`
  reflecting the `allow_recording` config, and the `voice_calls.id` stored in
  the artifact `payload`.
- **End** — when the last participant leaves the channel (`VoiceCallManager.
  end_call`), `ended_at`, `duration_seconds`, and `participant_count` are set on
  the `voice_calls` row and the linked artifact is transitioned to
  `ArtifactStatus.COMPLETED`.

The voice module wraps every call/artifact interaction so a failure there never
breaks voice: when the artifacts layer is unavailable (or the call manager is
not attached), calls simply are not recorded. Voice keeps working regardless.

### Recording flag

`config["artifacts"]["voice"]["allow_recording"]` gates whether calls may be
recorded. When it is `False`, `VoiceCallManager.mark_recorded(..., True)` is
clamped to `False` and the artifact's `recorded` flag is never set. The flag is
mirrored on both the `voice_calls` row and the linked `artifacts` row so the
history and the call metadata agree.

### Consent

`VoiceCallManager.add_consent(call_id, user_id)` appends a user to the
`consented_participants` JSON list on the `voice_calls` row (deduped). This is
the set of participants that agreed to recording; it is surfaced on the call
record and used by later groups to gate/annotate recordings.

### Transcript link

`VoiceCallManager.set_transcript(call_id, transcript_artifact_id)` links a
transcript artifact (produced by a later transcription group) to the call via
`transcript_artifact_id` and sets `has_transcript = True` on the linked
artifact.

## Transcription framework (`transcription/`)

`src/core/artifacts/transcription/` implements the voice-call transcription
framework. See `transcription/README.md` for the full detail; the essentials:

- **Providers** (`provider.py`): `LocalWhisperProvider` (runs OpenAI Whisper
  in-process), `OpenAIWhisperProvider` (real OpenAI Whisper API call), and
  `AzureSpeechProvider` (Azure Speech SDK or REST batch transcription). All
  return a `TranscriptionResult` with timestamped, optionally speaker-attributed
  segments. `get_transcription_provider(config)` is the single decision point
  and raises `ValueError` on inconsistent config (this becomes the capability
  `misconfigured` state).
- **Worker** (`worker.py`): `transcribe_call(call_id, db, config)` loads the
  `voice_calls` row, enforces gating (enabled + auto_transcribe + capability
  `AVAILABLE` + recorded + consent), resolves the recording reference from the
  linked `voice_call` artifact `payload["recording_ref"]`, runs the provider,
  creates a `TRANSCRIPT` artifact, links it via `set_transcript`, and emits
  `ARTIFACT_UPDATE`. `schedule_transcribe_call` is the fire-and-forget
  scheduler (asyncio task or bounded queue).
- **Auto-wiring**: when a call ends (`VoiceCallManager.end_call`, reached from
  the voice lifecycle hook in `src/core/voice/manager/calls.py`), and
  transcription is enabled + auto_transcribe + licensed, the transcription job
  is scheduled automatically.
- **Capability reuse**: `capabilities.py` evaluates `voice_transcription` by
  calling `get_transcription_provider(...).is_available()`, so the
  `DEPENDENCY_MISSING` / `MISCONFIGURED` state is the single source of truth
  shared by the admin panel and the worker.
- **DSAR**: `src/core/dsar/collector.py` includes the user's `voice_calls`
  (initiator or consented participant) and their `voice_call` / `transcript`
  artifacts, with transcript text surfaced inline.

## DSAR / privacy

Artifacts (including voice-call records and transcripts) are treated as
personal data and are covered by the DSAR (GDPR data-portability / erasure)
flows:

- **Export (right to data portability).** `src/core/dsar/collector.py`
  `_collect_voice` returns the user's `voice_states`, `voice_calls` (calls they
  initiated or consented to), and the linked `artifacts` rows of type
  `voice_call` and `transcript` that they authored. Transcript text is pulled
  out of the `payload` JSON into a readable `transcript_text` field so the
  JSON / ZIP export is human-readable without re-parsing segments. The DSAR
  preview counts (`count_records`) include `content_artifacts`,
  `content_transcripts`, and `voice_voice_calls` so the user sees how much
  artifact data will be exported. Every export format (JSON and ZIP, which
  dumps the whole collected dict) serializes these categories, so transcripts
  are always present in the download.
- **Deletion / anonymization (right to erasure).** On account purge the
  account reaper calls `anonymize_user_artifacts(db, user_id, config)` from
  `src/core/artifacts/privacy.py` (wired into `AccountReaper.purge_user`).
  This honors the same `anonymize_content` policy used for messages:
  - `anonymize_content=True` (default) — keeps the rows but removes the
    identifiable link: `author_id` is set to a sentinel anonymized id,
    inline transcript text is stripped from the `payload`, the user is removed
    from `consented_participants` on calls they only consented to, and their
    `actor_id` entries in `artifact_ops` are deleted. Calls they initiated are
    anonymized (initiator_id → sentinel) rather than deleted.
  - `anonymize_content=False` — deletes artifacts the user authored, the
    linked `artifact_ops` for those artifacts, and any `voice_calls` rows they
    initiated; consented-participant entries are removed from calls they did
    not start.

  The function returns the number of rows touched so the reaper's audit log can
  record the erasure.

### Manager surface (`voice_calls.py`)

`VoiceCallManager(db, artifact_manager=None, config=None)` exposes:

- `start_call(channel_id, server_id, initiator_id, conversation_id=None) -> VoiceCall`
- `end_call(call_id, participant_ids=None) -> VoiceCall`
- `mark_recorded(call_id, recorded: bool) -> VoiceCall`
- `add_consent(call_id, user_id) -> VoiceCall`
- `set_transcript(call_id, transcript_artifact_id) -> VoiceCall`
- `get_active_by_channel(channel_id) -> Optional[VoiceCall]`

## Models (`models.py`)

Pure dataclasses (same style as the messaging module) mapping to the tables
above. All IDs are Snowflake `int`s; boolean DB columns are exposed as `bool`.

- **`ArtifactType(str, Enum)`** — `VOICE_CALL`, `WHITEBOARD`, `UPLOAD`, `FILE`,
  `TRANSCRIPT`, `FUTURE` (values match the `artifact_type` column).
- **`ArtifactStatus(str, Enum)`** — `LIVE`, `COMPLETED`, `ARCHIVED`.
- **`Artifact`** — fields: `id`, `conversation_id`, `channel_id`, `server_id`,
  `author_id`, `artifact_type`, `title`, `summary`, `status`, `recorded`,
  `has_transcript`, `payload` (dict), `created_at`, `updated_at`,
  `retention_policy` (optional dict/str), `expires_at` (optional int),
  `license_feature` (optional str).
- **`VoiceCall`** — fields: `id`, `artifact_id`, `conversation_id`, `channel_id`,
  `server_id`, `initiator_id`, `started_at`, `ended_at`, `duration_seconds`,
  `recorded`, `transcript_artifact_id`, `consented_participants` (list[int]),
  `participant_count`, `created_at`, `updated_at`. Defined here for later
  voice-call groups but kept independent of call lifecycle logic.

## Repository (`repository.py`)

Functions operating on a `db` connection (parameterized SQL, no value
interpolation; sort keys restricted to an allow-list):

- `create_artifact(db, artifact) -> Artifact`
- `get_artifact(db, artifact_id) -> Optional[Artifact]`
- `update_artifact(db, artifact_id, **fields) -> Optional[Artifact]` — accepts
  only known columns; enum/bool/JSON fields are normalized.
- `delete_artifact(db, artifact_id) -> bool` — True if a row was removed.
- `list_artifacts(db, filters) -> list[Artifact]` — `filters` supports:
  `conversation_id`, `channel_id`, `server_id`, `author_id`, `artifact_type`
  (single value or a list), `status`, `recorded` (bool), `has_transcript`
  (bool), `search` (title/summary LIKE), `sort_by` (`created_at` | `title` |
  `type` | `duration` — `duration` joins `voice_calls`), `sort_order`
  (`asc` | `desc`), `limit`, `offset`.
- `count_artifacts(db, filters) -> int`
- `row_to_artifact(row)` / `artifact_to_row(artifact)` — row ↔ dataclass
  conversion (JSON columns encoded/decoded transparently).

## Manager (`manager.py`)

`ArtifactManager(BaseManager)` wraps the repository and the artifacts config.

- `__init__(db, config=None)` — config defaults to `utils.config.get("artifacts", {})`.
- `create(...)` — builds an `Artifact` with a fresh Snowflake id, derives
  `expires_at` from the resolved retention period (see below), and persists it.
- `get(artifact_id)`, `update(artifact_id, **fields)`, `delete(artifact_id)`
  (deletes the metadata row only; media purge / cascade cleanup of linked
  `voice_calls` and `artifact_ops` is deferred to later groups).
- `list_with_filters(filters, conversation_id=None, server_id=None,
  channel_id=None, author_id=None)` — merges scope args into the filters and
  delegates to `list_artifacts`. Visibility/permission enforcement is the
  responsibility of the route layer.
- `count(filters)`.
- `convert_upload_to_artifact(attachment, conversation_id, author_id,
  title=None, artifact_type=UPLOAD, server_id=None, channel_id=None) ->
  Artifact` — creates an `UPLOAD`/`FILE` artifact referencing an **existing**
  attachment. The `attachment` dict must carry an `attachment_id` (or `id`);
  `filename`, `content_type`, `size`, `url`, and `metadata` are copied into the
  artifact `payload`. This is the backend for the later retroactive-convert
  client flow and intentionally does not couple to the media module.

### Retention logic

`compute_expires_at(retention_days, created_at) -> Optional[int]` returns the
expiry timestamp in milliseconds, or `None` when `retention_days` is `None` or
non-positive (i.e. never expires). `_resolve_retention_days` computes the
effective period with this priority:

1. An explicit per-artifact `retention_policy` carrying `days`.
2. A per-server override — only when `allow_per_server_override` is set and
   `artifacts.servers.<server_id>.retention_days` exists.
3. The global `default_retention_days` (`None` ⇒ no expiry).

When `default_retention_days` is `None`, created artifacts never expire.

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

## REST API (Group 6)

The artifact REST API lives in `src/api/routes/artifacts.py` (user scope) and
`src/api/routes/admin/artifacts.py` (admin guarded), mounted under
`/api/v1/artifacts` and `/api/v1/admin/artifacts`. All user endpoints require
a valid auth token; admin endpoints additionally enforce host restriction and an
admin token.

### User endpoints

| Method | Path | Permission | Purpose |
|--------|------|------------|---------|
| `POST` | `/artifacts` | `artifact.create` | Create an artifact + emit a transcript message. |
| `GET` | `/artifacts` | `artifact.view` | List artifacts with filters. |
| `GET` | `/artifacts/{artifact_id}` | `artifact.view` | Fetch one artifact; `404` when missing. |
| `PATCH` | `/artifacts/{artifact_id}` | `artifact.edit` (or author) | Update mutable fields; `404` when missing. |
| `DELETE` | `/artifacts/{artifact_id}` | `artifact.delete` (or author) | Delete an artifact; `404` when missing. |
| `POST` | `/artifacts/convert-upload` | `artifact.create` | Convert an existing `msg_attachments` row into an `UPLOAD` artifact; `404` when the attachment is missing. |

`convert_upload` queries the real `msg_attachments` table (filtered on
`deleted = 0`) and returns `404` (not `500`) when no such attachment exists.
`create_artifact` maps its request body directly onto
`ArtifactManager.create(...)` (the `conversation_id`/`author_id`/`artifact_type`
`title`/`summary`/`channel_id`/`server_id`/`status`/`recorded`/`has_transcript`
`payload`/`retention_policy`/`license_feature` arguments match exactly). After a
create/convert, an inline `MESSAGE_CREATE` is emitted via
`events.create_message_create(message_id, channel_id, author_id, content,
server_id, author)` with `data["type"] = 0`, `data["message_type"] =
"artifact"`, and `data["metadata"] = {"artifact_id": ...}`.

### Admin endpoints

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/artifacts` | List all artifacts across every server. |
| `DELETE` | `/artifacts/{artifact_id}` | Force-delete any artifact. |
| `POST` | `/artifacts/retention/purge` | Run `purge_expired` (see below). Returns `{purged: <count>}`. |

### Permission names

The route layer resolves these permission names through the server RBAC
`require_permission`:

- `artifact.view`
- `artifact.create`
- `artifact.edit`
- `artifact.delete`
- `artifact.manage_retention` (reserved for retention management; the admin
  purge endpoint is admin-guarded instead).

## Retention purge (`retention.py`)

`purge_expired(db, config=None) -> int` deletes every row in the `artifacts`
table where `expires_at IS NOT NULL AND expires_at <= <now-ms>` and returns the
number of rows removed (`0` on a `None` db or on error). This is the real
implementation backing the admin `/artifacts/retention/purge` endpoint. Media
and linked-row cascade cleanup remain out of scope, matching the manager's
`delete` semantics.

### Scheduled cleanup job

`RetentionCleanupJob(db, config=None)` is a scheduled background worker (daemon
thread) mirroring the `AccountReaper` pattern. It is started at boot by
`src/server/initializer.py` (stored in `modules_store["artifact_retention"]`)
and runs only when `artifacts.retention.run_cleanup_interval_minutes > 0`.

Each cycle calls `run_once(db)` which:

1. `_apply_retention_windows(db, config)` — for every artifact that has a
   `retention_policy` but a `NULL expires_at`, stamps `expires_at =
   created_at + <resolved retention days>`. Artifacts with no resolvable
   retention period are left untouched (never expire).
2. `purge_expired(db, config)` — removes rows whose window has elapsed. When
   `artifacts.retention.purge_expired` is `False`, the cycle only applies
   windows and does not delete anything.

The loop sleeps `run_cleanup_interval_minutes` (in minutes) between cycles,
with the sleep broken into small increments so `stop()` can shut it down
promptly.

### Per-server override

`resolve_retention_days(server_id, config=None) -> Optional[int]` resolves the
effective retention period (in days) for a server, returning `None` to mean
"never expire". Resolution priority:

1. A per-server override from the `server_artifact_settings` table
   (`retention_days`), but only when `artifacts.allow_per_server_override` is
   `True` and a row exists for the server. The table is created by migration
   `048_add_server_artifact_settings.py`.
2. The global `artifacts.default_retention_days` (`None` ⇒ never expire by
   default).

**No expire by default:** when `default_retention_days` is `None` (the
documented default), artifacts never expire unless a per-artifact
`retention_policy` or a per-server override explicitly sets a positive number
of days. The cleanup interval gates how often the job runs, not whether data is
deleted.

## Real-time fabric

The collaborative layer that lets live whiteboards and shared code editors
sync in real time is implemented in the WebSocket gateway (Group 5), on top of
the schema and domain models documented above. It does not touch the REST
routes (those arrive in a later group).

### Gateway opcodes (60-63)

Defined in `src/api.websocket.opcodes.GatewayOpcode`:

- `ARTIFACT_SUBSCRIBE = 60` — client subscribes to an artifact's live updates.
- `ARTIFACT_UNSUBSCRIBE = 61` — client withdraws a subscription.
- `ARTIFACT_OP = 62` — a realtime delta op for an artifact; relayed to the
  artifact's other subscribers.
- `ARTIFACT_SYNC = 63` — a full snapshot, sent to late joiners.

### Event types

Defined in `src.core.events.types.EventType`: `ARTIFACT_CREATE`,
`ARTIFACT_UPDATE`, `ARTIFACT_DELETE`, `ARTIFACT_OP`.

### Gateway intent

`src.core.events.types.GatewayIntent.ARTIFACTS = 1 << 20` gates delivery of
the artifact event types. It is part of both `default_intents()` and
`all_intents()`.

### Relay helpers

`src/api/websocket/artifacts.py` owns the subscription registry
(`subscribe` / `unsubscribe` / `get_subscribers`) and the async relay
helpers used by the opcode handlers:

- `relay_artifact_op(dispatcher, artifact_id, op, actor_id, exclude_user_id)`
  fans an `ARTIFACT_OP` out to all other subscribers of an artifact. It only
  relays — persistence of the op is the responsibility of a later group
  (editor / ops persistence).
- `send_artifact_sync(connection, artifact_id, snapshot)` emits an
  `ARTIFACT_SYNC` full snapshot to a single connection (bootstrap for late
  joiners). The snapshot dict is fetched by the caller.

Delivery reuses the existing dispatcher send path and the per-connection rate
limit; no new rate-limit mechanism is introduced.

## Federation (PlexiJoin)

When two Plexichat instances are federated via PlexiJoin, artifacts that belong
to a federated server can have their live operations and artifact-level events
shared across the link. The integration lives in
`src/core/artifacts/federation.py` (`FederationArtifactBridge`) and integrates at
the manager level — it does **not** assume or build a cross-instance realtime
transport (that external layer lives outside this repo).

### `FederationArtifactBridge`

Constructed with `(db, plexijoin_manager)`. Responsibilities:

- Resolves an artifact's owning `server_id` (via the artifacts repository) and
  maps it to the active federation connection(s) for that server. Links are
  matched on `plexijoin_connections.remote_instance_id == server_id` and only
  `active` links are considered.
- `forward_artifact_op(artifact_id, op, actor_id)` — after a local relay,
  forwards a live `ARTIFACT_OP` delta to the matching federation connection(s).
- `forward_artifact_event(event_type, artifact)` — forwards an artifact-level
  event (create/update/delete) to the matching federation connection(s).
- `get_federated_artifact_visibility(server_id) -> bool` — returns whether
  artifacts on `server_id` are shareable cross-instance (i.e. at least one
  active federation link exists for that server). Exposed to the
  capability/visibility layer.

Both `forward_*` methods account for the forwarded traffic against each
federation connection via `PlexiJoinManager.record_traffic` (so federation
accounting stays accurate) and then invoke an injectable transport callback.

### Injectable transport

The real cross-instance transport is registered via
`set_federation_transport(fn)`. The injected callable receives
`(artifact_id, payload, actor_id, connection_ids)` and is responsible for
actually shipping the payload to each remote instance. The **default transport is
real, not a stub**: it records the traffic (via `record_traffic`) and logs the
forwarding intent. A deployment that wires up the external transport simply
replaces it with a function that performs the network send.

### Wiring & hook

- The initializer constructs `FederationArtifactBridge(db, plexijoin_manager)`
  when PlexiJoin is licensed and its encryption service is available, and attaches
  it to the WS artifact layer via `set_artifact_federation_bridge(bridge)`.
- `relay_artifact_op` (in `src/api/websocket/artifacts.py`) calls
  `bridge.forward_artifact_op(...)` after the local fan-out. The federation call
  is wrapped in try/except and never breaks local relay if federation is
  unavailable.

### Permissions

The bridge only **forwards** ops/events. It never auto-grants local permissions
to remote participants — remote participants are not local users, and visibility
of federated artifacts remains gated by the local route/permission layer.
