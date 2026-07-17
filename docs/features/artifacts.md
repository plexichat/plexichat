# Artifacts

Artifacts are persistent, shareable pieces of rich content attached to Plexichat
conversations: code files edited in-app, collaborative whiteboards, and voice-call
recordings/transcripts. This document describes the `artifacts` configuration block
and the license features that gate it.

## License Features

The Artifacts system is gated by three license feature flags, checked at runtime via
`utils.licensing.has_feature(<name>)`. They are documented in
`ARTIFACTS_LICENSE_FEATURES` in `src/config_defaults.py`.

| Feature | Gates |
| --- | --- |
| `artifacts` | Master artifacts feature (any artifact type). The whole `artifacts` block requires it. |
| `artifacts_whiteboard` | Licensed multi-user live whiteboard artifacts (`artifacts.whiteboard`). |
| `voice_transcription` | Licensed automatic voice-call transcription (`artifacts.voice.transcription`). |

## Configuration

All settings are nested under the top-level `artifacts` key. Defaults are defined in
`get_default_config()` in `src/config_defaults.py`.

```yaml
artifacts:
  enabled: true
  default_retention_days: null
  allow_per_server_override: true
  max_artifact_size_mb: 200
  editor:
    enabled: true
    allowed_languages:
    - python
    - javascript
    - typescript
    - json
    - markdown
    - go
    - rust
    - sql
    - yaml
    - html
    - css
    max_file_size_mb: 50
  whiteboard:
    enabled: false
    licensed_feature: artifacts_whiteboard
    max_participants: 50
    persist_ops: true
    op_rate_per_sec: 30
  voice:
    allow_recording: true
    transcription:
      provider: local_whisper
      enabled: false
      auto_transcribe: false
      language: auto
      diarize: false
      model_size: base
      whisper_probe_on_startup: true
      openai_api_key: ${OPENAI_API_KEY:-}
      azure_key: ${AZURE_SPEECH_KEY:-}
      max_audio_minutes: 120
    transcript_retention_days: null
  retention:
    run_cleanup_interval_minutes: 60
    purge_expired: true
```

### Top-level keys

| Key | Default | Meaning |
| --- | --- | --- |
| `enabled` | `true` | Master switch for the artifacts subsystem. |
| `default_retention_days` | `null` | Default artifact lifetime in days. `null` means artifacts never expire by default. |
| `allow_per_server_override` | `true` | Whether individual servers may override artifact settings. |
| `max_artifact_size_mb` | `200` | Maximum size (MB) for any single artifact. |

### `editor`

| Key | Default | Meaning |
| --- | --- | --- |
| `enabled` | `true` | Enables the in-app code/text editor artifact type. |
| `allowed_languages` | list | Syntax-highlighting/editor languages permitted. |
| `max_file_size_mb` | `50` | Maximum size (MB) for a single editor file. |

### `whiteboard`

Requires the `artifacts_whiteboard` license feature.

| Key | Default | Meaning |
| --- | --- | --- |
| `enabled` | `false` | Enables collaborative whiteboard artifacts. |
| `licensed_feature` | `artifacts_whiteboard` | License feature checked before whiteboards run. |
| `max_participants` | `50` | Maximum concurrent editors per whiteboard. |
| `persist_ops` | `true` | Persist whiteboard operations for replay/history. |
| `op_rate_per_sec` | `30` | Max whiteboard operations per second per client. |

### `voice`

| Key | Default | Meaning |
| --- | --- | --- |
| `allow_recording` | `true` | Whether voice calls may be recorded. |
| `transcript_retention_days` | `null` | Transcript lifetime in days. `null` means never expire by default. |

#### `voice.transcription`

Requires the `voice_transcription` license feature. **Off until configured:** the
`enabled` key defaults to `false`, so no transcription happens until an operator
explicitly turns it on (and, for `local_whisper`, whisper is present).

| Key | Default | Meaning |
| --- | --- | --- |
| `provider` | `local_whisper` | Transcription backend; active only when `enabled: true` and the backend is available. |
| `enabled` | `false` | Master switch for transcription. Off until explicitly configured. |
| `auto_transcribe` | `false` | Automatically transcribe recorded calls. |
| `language` | `auto` | Source language, or `auto` for detection. |
| `diarize` | `false` | Attempt speaker diarization. |
| `model_size` | `base` | Whisper model size for `local_whisper`. |
| `whisper_probe_on_startup` | `true` | Probe for a working whisper install at startup. |
| `openai_api_key` | `${OPENAI_API_KEY:-}` | API key for the OpenAI transcription provider (env-interpolated). |
| `azure_key` | `${AZURE_SPEECH_KEY:-}` | API key for the Azure Speech provider (env-interpolated). |
| `max_audio_minutes` | `120` | Maximum audio length (minutes) accepted for transcription. |

#### Transcription framework

The `transcription/` subpackage implements the voice-call transcription
framework (full detail in `src/core/artifacts/transcription/README.md`):

- **Providers** (`provider.py`): `LocalWhisperProvider` (runs OpenAI Whisper
  in-process, lazy model load), `OpenAIWhisperProvider` (real OpenAI Whisper
  API call), `AzureSpeechProvider` (Azure Speech SDK or REST batch
  transcription). All return a `TranscriptionResult` with timestamped,
  optionally speaker-attributed segments. `get_transcription_provider(config)`
  is the single backend-selection decision point and raises `ValueError` on
  inconsistent config (surfaced as the capability `misconfigured` state).
- **Worker** (`worker.py`): `transcribe_call(call_id, db, config)` loads the
  `voice_calls` row, enforces gating (enabled + auto_transcribe + capability
  `AVAILABLE` + recorded + consent), resolves the recording reference from the
  linked `voice_call` artifact `payload["recording_ref"]`, runs the provider,
  creates a `TRANSCRIPT` artifact, links it via `set_transcript`, and emits
  `ARTIFACT_UPDATE`. `schedule_transcribe_call` is the fire-and-forget
  scheduler used from the voice lifecycle hook on call end.
- **Consent**: when `consent_required` is `true` (default), a call must have at
  least one consented participant or transcription is skipped (GDPR-safe).

### `retention`

| Key | Default | Meaning |
| --- | --- | --- |
| `run_cleanup_interval_minutes` | `60` | How often the retention cleanup task runs. |
| `purge_expired` | `true` | Delete artifacts/transcripts past their retention window. |

#### Scheduled cleanup job

`RetentionCleanupJob` (in `src/core/artifacts/retention.py`) is a daemon-thread
background worker started at boot by the initializer. It runs only when
`run_cleanup_interval_minutes > 0`. Each cycle calls `run_once(db)`, which:

1. Applies retention windows — stamps `expires_at = created_at + <retention
   days>` for any artifact that has a `retention_policy` but no `expires_at`
   yet (via `_apply_retention_windows`).
2. Purges expired rows (`purge_expired`). When `purge_expired` is `false`, the
   job only applies windows and never deletes.

The admin `POST /artifacts/retention/purge` endpoint calls `purge_expired`
directly to trigger an on-demand purge.

#### Per-server override & "no expire by default"

Retention resolution honors the **no expire by default** rule: with
`default_retention_days: null` (the default), artifacts never expire unless an
explicit per-artifact `retention_policy` (carrying `days`) or a per-server
override sets a positive day count.

When `allow_per_server_override: true`, individual servers may override the
global default via the `server_artifact_settings` table (created by migration
`048_add_server_artifact_settings.py`). `resolve_retention_days(server_id,
config)` resolves the effective period with this priority:

1. The server's `retention_days` row in `server_artifact_settings` (only if
   `allow_per_server_override` is enabled and a row exists).
2. The global `default_retention_days` (`null` ⇒ never expire).

This same resolution already drives `ArtifactManager.create(...)` when an
artifact is first written, so the scheduled job keeps already-stored rows
consistent with the active policy.

## Database Schema

The Artifacts feature persists its data in three tables, created by migration
`047_add_artifacts_tables.py` (which calls `create_tables` from
`src/core/artifacts/schema.py`).

- **`artifacts`** — the central record for every artifact. Key columns:
  `id` (Snowflake INTEGER PK), `conversation_id`, `channel_id`, `server_id`,
  `author_id`, `artifact_type` (`voice_call`, `whiteboard`, `upload`, `file`,
  `transcript`, `future`), `title`, `summary`, `status`
  (`live`, `completed`, `archived`), `recorded` (0/1), `has_transcript` (0/1),
  `payload` (JSON TEXT), `retention_policy`, `expires_at`, `license_feature`,
  `created_at`, `updated_at`. Indexed on `conversation_id`, `server_id`,
  `author_id`, `artifact_type`, `created_at`.
- **`voice_calls`** — call-specific metadata linked to `artifacts` via
  `artifact_id`. Columns include `initiator_id`, `started_at`, `ended_at`,
  `duration_seconds`, `recorded` (0/1), `transcript_artifact_id`,
  `consented_participants` (JSON TEXT), `participant_count`. Indexed on
  `artifact_id`, `conversation_id`, `server_id`.
- **`artifact_ops`** — an ordered, append-only operations log for collaborative
  artifacts. Columns: `id` (AUTOINCREMENT), `artifact_id`, `seq`, `op_type`,
  `actor_id`, `data` (JSON TEXT), `created_at`, with `UNIQUE(artifact_id, seq)`
  and indexes on `artifact_id` and `(artifact_id, seq)`.

See [`src/core/artifacts/README.md`](../src/core/artifacts/README.md) for the
full table/column reference.

## Capabilities & Availability Banners

Because several artifacts features can be unavailable for different reasons
(disabled in config, missing license, missing dependency, or missing API key),
the backend exposes a **capability service** that resolves each feature to a
single availability state. The client and admin panel use this to render
availability banners explaining *why* a feature is off instead of silently
failing.

The service lives in `src/core/artifacts/capabilities.py` and evaluates these
features:

| Feature | Notes |
| --- | --- |
| `artifacts` | Master switch. |
| `artifacts_editor` | In-app editor artifact type. |
| `artifacts_whiteboard` | Requires the `artifacts_whiteboard` license feature. |
| `voice_transcription` | Requires the `voice_transcription` license feature; provider-dependent (local Whisper, OpenAI, Azure). |
| `voice_recording` | Whether voice calls may be recorded. |

Each feature resolves to exactly one `CapabilityState`:

- `available` — fully usable.
- `disabled_by_config` — turned off in server config.
- `disabled_by_license` — required license feature is absent.
- `dependency_missing` — a runtime dependency (e.g. Whisper) is not installed.
- `misconfigured` — enabled and licensed, but required configuration (e.g. an
  API key) is missing, or the provider is unknown.

### Endpoints

- `GET /api/v1/capabilities` (auth required) — returns the per-feature capability
  dict `{feature: {state, message, details}}` for client-side availability notices.
- `GET /api/v1/admin/capabilities` (admin guarded) — returns the same per-feature
  breakdown plus a top-level `summary` with counts and a `by_state` grouping
  (useful for the admin dashboard's availability overview).

See [`src/core/artifacts/README.md`](../src/core/artifacts/README.md) for the
full capability-service reference.

## REST API (Group 6)

The artifact REST API is mounted under `/api/v1/artifacts` (user scope) and
`/api/v1/admin/artifacts` (admin guarded). All user endpoints require a valid
auth token; the admin endpoints additionally enforce host restriction and an
admin token.

### User endpoints (`/api/v1/artifacts`)

| Method | Path | Permission | Purpose |
|--------|------|------------|---------|
| `POST` | `/artifacts` | `artifact.create` | Create an artifact + emit a transcript message. |
| `GET` | `/artifacts` | `artifact.view` | List artifacts with filters (type, status, author, search, paging). |
| `GET` | `/artifacts/{artifact_id}` | `artifact.view` | Fetch one artifact; `404` when missing. |
| `PATCH` | `/artifacts/{artifact_id}` | `artifact.edit` (or author) | Update mutable fields; `404` when missing. |
| `DELETE` | `/artifacts/{artifact_id}` | `artifact.delete` (or author) | Delete an artifact; `404` when missing. |
| `POST` | `/artifacts/convert-upload` | `artifact.create` | Convert an existing `msg_attachments` row into an `UPLOAD` artifact; `404` when the attachment is missing. |

Permission checks defer to the server RBAC layer for server-scoped actions. For
DM/group conversations (no server), the caller must be a participant/owner of
the conversation. The inline transcript message uses the `artifact` message type
and references the artifact via `metadata.artifact_id`.

### Admin endpoints (`/api/v1/admin/artifacts`)

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/artifacts` | List all artifacts across every server (admin oversight). |
| `DELETE` | `/artifacts/{artifact_id}` | Force-delete any artifact. |
| `POST` | `/artifacts/retention/purge` | Run `purge_expired` to delete artifacts whose `expires_at` is set and already in the past. Returns `{purged: <count>}`. |

### Permission names

The route layer uses the following permission names (resolved via the server
RBAC `require_permission`):

- `artifact.view`
- `artifact.create`
- `artifact.edit`
- `artifact.delete`
- `artifact.manage_retention` (reserved for retention management; the admin
  purge endpoint is admin-guarded instead).

## Core Manager

The domain logic for artifacts lives in `src/core/artifacts/` and is intentionally
self-contained — no routes, websocket handlers, or voice-call lifecycle code.
It is split into three layers:

- **`models.py`** — dataclasses `Artifact`, `VoiceCall`, and the `ArtifactType`
  (`voice_call`, `whiteboard`, `upload`, `file`, `transcript`, `future`) and
  `ArtifactStatus` (`live`, `completed`, `archived`) enums. These mirror the
  `artifacts` and `voice_calls` table columns; boolean DB columns are exposed as
  `bool` and JSON columns as `dict`/`list`.
- **`repository.py`** — parameterized data-access functions (`create_artifact`,
  `get_artifact`, `update_artifact`, `delete_artifact`, `list_artifacts`,
  `count_artifacts`) plus `row_to_artifact` / `artifact_to_row`. Sort keys are
  restricted to an allow-list (`created_at`, `title`, `type`, `duration`) so no
  user input is interpolated into SQL.
- **`manager.py`** — `ArtifactManager(BaseManager)` wraps the repository and the
  `artifacts` config block. Public surface: `create`, `get`, `update`, `delete`,
  `list_with_filters`, `count`, and `convert_upload_to_artifact`.

### Retention

`ArtifactManager.create(...)` derives `expires_at` (a millisecond timestamp, or
`NULL`/never-expire) from the effective retention period resolved by
`_resolve_retention_days`, with this priority:

1. An explicit per-artifact `retention_policy` carrying `days`.
2. A per-server override — only when `allow_per_server_override` is `true` and
   `artifacts.servers.<server_id>.retention_days` exists.
3. The global `default_retention_days` (`null` ⇒ never expire).

The helper `compute_expires_at(retention_days, created_at) -> Optional[int]`
returns `None` when `retention_days` is `None` or non-positive, so callers can
distinguish "never expires" from a concrete timestamp.

### Upload conversion

`convert_upload_to_artifact(attachment, conversation_id, author_id, ...)` creates
an `UPLOAD`/`FILE` artifact that references an **existing** attachment (media is
not re-uploaded). The `attachment` dict must carry `attachment_id` (or `id`);
`filename`, `content_type`, `size`, `url`, and `metadata` are copied into the
artifact `payload`. This is the backend for the retroactive-convert client flow
and deliberately does not couple to the media module.

### Voice call artifacts

When a voice call ends it produces a `voice_calls` record plus a corresponding
`voice_call` Artifact, so the call appears in chat history and the artifacts
pane. The call lifecycle is driven by channel occupancy: it starts when the
first participant joins a voice channel and ends when the last participant
leaves.

- **Start** — `VoiceCallManager.start_call(channel_id, server_id, initiator_id,
  conversation_id=None)` inserts a `voice_calls` row (`started_at` now,
  `participant_count` 1) and creates a linked `voice_call` Artifact in `LIVE`
  status via `ArtifactManager.create(...)`, storing the `voice_calls.id` in the
  artifact `payload`.
- **End** — `VoiceCallManager.end_call(call_id, participant_ids=None)` sets
  `ended_at`, `duration_seconds`, and `participant_count`, then transitions the
  linked artifact to `COMPLETED`.
- **Recording** — `config.artifacts.voice.allow_recording` gates recording. When
  it is `false`, `mark_recorded(call_id, True)` is clamped to `false` and the
  artifact's `recorded` flag is never set. The flag is mirrored on both the
  `voice_calls` row and the linked artifact.
- **Consent** — `add_consent(call_id, user_id)` appends to the
  `consented_participants` JSON list (deduped) on the `voice_calls` row.
- **Transcript** — `set_transcript(call_id, transcript_artifact_id)` links a
  transcript artifact (from the transcription group) and sets `has_transcript =
  true` on the linked artifact.

All call/artifact interactions are wrapped defensively: if the artifacts layer
is unavailable the voice pipeline keeps working and simply omits the call
record.

## Real-time fabric (WebSocket)

Collaborative artifacts (live whiteboards, shared code editors, and similar
canvases) sync over the WebSocket gateway using a small real-time fabric. The
REST routes documented elsewhere remain the source of truth for persisted
state; the fabric only relays in-memory deltas and snapshots.

### Gateway opcodes (60-63)

Defined in `src/api/websocket/opcodes.GatewayOpcode`:

| Opcode | Name | Direction | Purpose |
|--------|------|-----------|---------|
| 60 | `ARTIFACT_SUBSCRIBE` | C→S | Subscribe to an artifact's live updates |
| 61 | `ARTIFACT_UNSUBSCRIBE` | C→S | Unsubscribe from an artifact |
| 62 | `ARTIFACT_OP` | C→S→C | Relay a realtime delta op to subscribers |
| 63 | `ARTIFACT_SYNC` | S→C | Full snapshot for late joiners |

### Event types and intent

- `src/core/events/types.EventType`: `ARTIFACT_CREATE`, `ARTIFACT_UPDATE`,
  `ARTIFACT_DELETE`, `ARTIFACT_OP`.
- `src/core/events/types.GatewayIntent.ARTIFACTS = 1 << 20` gates delivery of
  those events and is part of both `default_intents()` and `all_intents()`.

### Relay helpers

`src/api/websocket/artifacts.py` owns the in-memory subscription registry
(`ArtifactSubscriptionRegistry`: `subscribe` / `unsubscribe` /
`get_subscribers` / `unsubscribe_all`) plus two async helpers used by the
opcode handlers:

- `relay_artifact_op(dispatcher, artifact_id, op, actor_id, exclude_user_id)` —
  fans an `ARTIFACT_OP` to every other subscriber of an artifact. It only
  relays; persistence is the job of a later group (editor / ops persistence).
- `send_artifact_sync(connection, artifact_id, snapshot)` — emits an
  `ARTIFACT_SYNC` full snapshot to a single connection (late-joiner bootstrap).

Both reuse the existing dispatcher send path and the per-connection rate limit;
no new rate-limit mechanism is introduced.

## Federation (PlexiJoin)

When two Plexichat instances are federated via PlexiJoin, artifacts owned by a
federated server can share their live operations and artifact-level events across
the link. The integration is implemented at the manager level in
`src/core/artifacts/federation.py` (`FederationArtifactBridge`) and does **not**
build a cross-instance realtime transport (that external layer lives outside this
repo).

### How it works

- **Server → link resolution.** `FederationArtifactBridge` resolves an
  artifact's owning `server_id` (via the artifacts repository) and maps it to the
  active federation connection(s) for that server. Links are matched on
  `plexijoin_connections.remote_instance_id == server_id` and only `active`
  links are considered.
- **Op forwarding.** `relay_artifact_op` in `src/api/websocket/artifacts.py`
  calls `bridge.forward_artifact_op(artifact_id, op, actor_id)` immediately after
  the local fan-out. The call is wrapped in try/except so a federation failure
  never breaks local relay.
- **Event forwarding.** `forward_artifact_event(event_type, artifact)` shares
  artifact create/update/delete events to the matching links.
- **Traffic accounting.** Every forwarded op/event is recorded against the
  resolved federation connections via `PlexiJoinManager.record_traffic`, keeping
  federation accounting accurate.
- **Visibility.** `get_federated_artifact_visibility(server_id)` returns
  whether artifacts on that server are shareable cross-instance (at least one
  active link exists). Exposed to the capability/visibility layer.

### Injectable transport

The real cross-instance transport is registered with
`set_federation_transport(fn)`; the callable receives
`(artifact_id, payload, actor_id, connection_ids)`. The **default transport is
real** (records traffic + logs the forwarding intent), not a stub; a deployment
replaces it with a function that performs the actual network send when the
external transport is available.

### Wiring

The initializer builds `FederationArtifactBridge(db, plexijoin_manager)` only
when PlexiJoin is licensed and its encryption service is available, then attaches
it to the WS artifact layer with `set_artifact_federation_bridge(bridge)`.

### Permissions

The bridge only **forwards** ops/events. It never auto-grants local permissions
to remote participants — remote participants are not local users, so visibility of
federated artifacts remains gated by the local route/permission layer.

## DSAR / Privacy

Artifacts, voice-call records, and transcripts are personal data and are fully
covered by Plexichat's DSAR (GDPR data-portability and erasure) flows.

### Export (right to data portability)

The DSAR collector (`src/core/dsar/collector.py`) includes, in the `voice`
category:

- the user's `voice_states`,
- `voice_calls` they initiated or consented to (`consented_participants`),
- the `voice_call` and `transcript` `artifacts` they authored.

Transcript text is surfaced inline as a readable `transcript_text` field
(extracted from the `payload` JSON) so the downloaded export does not require
re-parsing segment arrays. The DSAR preview counts (`count_records`) expose
`content_artifacts`, `content_transcripts`, and `voice_voice_calls` so the
requester sees exactly how much artifact data will be exported. Because every
export format (JSON and ZIP) serializes the whole collected dict, transcripts
and artifact metadata are always present in the download.

### Deletion / anonymization (right to erasure)

On account purge, the account reaper calls
`anonymize_user_artifacts(db, user_id, config)` from
`src/core/artifacts/privacy.py`, wired into `AccountReaper.purge_user`. It
honors the same `anonymize_content` policy already applied to messages:

- `anonymize_content=True` (default) — rows are kept but the user's
  identifiable link is removed: `author_id` → sentinel anonymized id, inline
  transcript text is stripped from the `payload`, the user is removed from
  `consented_participants` on calls they only consented to, and their
  `actor_id` operations in `artifact_ops` are deleted. Calls they initiated are
  anonymized (`initiator_id` → sentinel) rather than removed.
- `anonymize_content=False` — artifacts the user authored, their linked
  `artifact_ops`, and any `voice_calls` rows they initiated are deleted;
  consented-participant entries are removed from calls they did not start.

The function returns the number of rows touched so the erasure is auditable.

## Admin Panel

The server-rendered admin dashboard exposes an **Artifacts** sidebar tab
(`data-tab="artifacts"`) for instance-level oversight and maintenance of the
Artifacts subsystem. It uses the existing `api()` fetch helper and `alert()`/
`confirm()` patterns from `dashboard.js` and renders three cards:

### Capabilities banners

`renderArtifactCapabilities()` calls `GET /api/v1/admin/capabilities` and renders
one banner per feature (`artifacts`, `artifacts_editor`, `artifacts_whiteboard`,
`voice_transcription`, `voice_recording`). Each banner shows the feature name,
its availability `state`, and the backend `message` (plus `details` when present).
Banner color is driven by `state`:

| `state` | Banner color | Meaning |
| --- | --- | --- |
| `available` | green | Feature is fully usable. |
| `disabled_by_config` | amber | Turned off in server config. |
| `disabled_by_license` | red | Required license feature is absent. |
| `dependency_missing` | red | A runtime dependency (e.g. Whisper) is not installed. |
| `misconfigured` | red | Enabled/licensed but required config (e.g. API key) is missing. |

### Artifact list & force-delete

`renderArtifactList()` calls `GET /api/v1/admin/artifacts` and renders a table of
every artifact across all servers (type, title, author, server, created,
retention, expires). Each row has a **Force delete** button wired to
`forceDeleteArtifact()`, which confirms with the operator then calls
`DELETE /api/v1/admin/artifacts/{id}` and re-renders the table on success. A
**Refresh** button re-fetches both capabilities and the list.

### Retention controls

- **Run retention purge** — `runRetentionPurge()` confirms then `POST`s to
  `/api/v1/admin/artifacts/retention/purge`, which deletes artifacts whose
  `expires_at` is already in the past (returns `{purged: <count>}`). The count is
  surfaced in a toast and the table refreshes.
- **Per-server retention override** — a form (`saveServerRetention()`) that
  `POST`s `{server_id, retention_days}` to
  `/api/v1/admin/artifacts/retention/server`. A blank `retention_days` clears the
  override so the server reverts to the global `default_retention_days` (`null` ⇒
  never expire by default). The response echoes the effective override.

All failures surface via `alert()` with the API error message.

## Related Documentation

- [Default Configuration Reference](../default-config.md) - Complete configuration reference
- [Feature Overview](../features.md) - All Plexichat features
