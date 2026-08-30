# Artifacts

Artifacts are persistent, shareable content attached to Plexichat conversations: collaborative documents, code files, whiteboards, voice-call recordings, and transcripts. This document describes the `artifacts` configuration block and the license features that gate it.

## License features

The Artifacts system uses license feature flags checked at runtime with `utils.licensing.has_feature(name)`. The flags are defined in `ARTIFACTS_LICENSE_FEATURES` (`src/config_defaults.py:24`).

| Feature | What it gates |
| --- | --- |
| `artifacts` | Master switch for any artifact type. The whole `artifacts` block requires it. |
| `artifacts_whiteboard` | Live whiteboard artifacts (`artifacts.whiteboard`). |
| `voice_transcription` | Automatic voice-call transcription (`artifacts.voice.transcription`). |
| `plexiscribe` | Collaborative document editor (`artifacts.plexiscribe`). |
| `plexiscript` | Collaborative code editor (`artifacts.plexiscript`). |
| `plexistash` | PlexiStash metadata vault (`artifacts.plexistash`). |
| `plexisum` | PlexiSum collaborative workbooks (`artifacts.plexisum`). |

## Configuration

All settings are nested under the top-level `artifacts` key. Defaults are defined in `get_artifacts_defaults` (`src/config_defaults_artifacts.py:12`).

```yaml
artifacts:
  enabled: true
  default_retention_days: null
  allow_per_server_override: true
  max_artifact_size_mb: 200
  evaluation:
    enabled: false
    ttl_days: 30
  plexiscribe:
    enabled: true
    licensed_feature: plexiscribe
    max_document_size_mb: 100
    max_participants: 50
  plexiscript:
    enabled: true
    licensed_feature: plexiscript
    max_file_size_kb: 2048
    max_participants: 50
    allowed_languages: [python, javascript, typescript, json, markdown, go, rust, sql, yaml, html, css]
    # Full language and extension lists are in src/config_defaults_artifacts.py:40
  plexistash:
    enabled: true
    licensed_feature: plexistash
    cleanup_interval_minutes: 60
    retention_days: 30
  plexisum:
    enabled: true
    licensed_feature: plexisum
  whiteboard:
    enabled: true
    licensed_feature: artifacts_whiteboard
    max_participants: 50
    persist_ops: true
    op_rate_per_sec: 30
  voice:
    allow_recording: true
    recording:
      output_dir: ~/.plexichat/data/recordings
      max_duration_minutes: 120
      format: webm
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
    suite_deleted_retention_days: 30
```

### Top-level keys

| Key | Default | Description |
| --- | --- | --- |
| `enabled` | `true` | Master switch for the artifacts subsystem. |
| `default_retention_days` | `null` | Default lifetime in days. `null` means artifacts never expire unless a per-artifact or per-server policy sets a value. |
| `allow_per_server_override` | `true` | Whether individual servers can override the global retention with a row in `server_artifact_settings`. |
| `max_artifact_size_mb` | `200` | Maximum size for any single artifact. |
| `evaluation` | `enabled: false, ttl_days: 30` | When `enabled`, the licensed Suite apps run in an ephemeral evaluation state without a license; content is auto-deleted after `ttl_days` by the retention job. |

### Plexiscribe

Requires the `plexiscribe` license feature (or evaluation mode).

| Key | Default | Description |
| --- | --- | --- |
| `enabled` | `true` | Enables the collaborative document editor. |
| `max_document_size_mb` | `100` | Maximum size per document. |
| `max_participants` | `50` | Maximum concurrent editors. |

### Plexiscript

Requires the `plexiscript` license feature (or evaluation mode). The editor language and extension allow-lists are defined in `src/config_defaults_artifacts.py:40`.

| Key | Default | Description |
| --- | --- | --- |
| `enabled` | `true` | Enables the collaborative code editor. |
| `max_file_size_kb` | `2048` | Maximum size per file. |
| `max_participants` | `50` | Maximum concurrent editors. |
| `allowed_languages` | See file | Syntax highlighting languages that are permitted. |
| `allowed_extensions` | See file | File extensions that are permitted. |
| `blocked_extensions` | See file | File extensions that are always rejected. |

### Whiteboard

Requires the `artifacts_whiteboard` license feature (or evaluation mode).

| Key | Default | Description |
| --- | --- | --- |
| `enabled` | `true` | Enables collaborative whiteboard artifacts. |
| `max_participants` | `50` | Maximum concurrent editors per whiteboard. |
| `persist_ops` | `true` | Persist whiteboard operations for replay and history. |
| `op_rate_per_sec` | `30` | Maximum operations per second per client. |

### Voice

| Key | Default | Description |
| --- | --- | --- |
| `allow_recording` | `true` | Whether voice calls can be recorded. |
| `recording.output_dir` | `~/.plexichat/data/recordings` | Directory for local recording files. |
| `recording.max_duration_minutes` | `120` | Maximum recording duration. |
| `recording.format` | `webm` | Recording container format. |
| `transcript_retention_days` | `null` | Transcript lifetime in days. `null` means never expire by default. |

#### Voice transcription

Requires the `voice_transcription` license feature. Transcription is off until you explicitly set `enabled: true` and the backend is available.

| Key | Default | Description |
| --- | --- | --- |
| `provider` | `local_whisper` | Backend: `local_whisper`, `openai`, or `azure`. Active only when `enabled` is `true` and the backend is available. |
| `enabled` | `false` | Master switch for transcription. |
| `auto_transcribe` | `false` | Automatically transcribe recorded calls. |
| `language` | `auto` | Source language, or `auto` for detection. |
| `diarize` | `false` | Attempt speaker diarization. |
| `model_size` | `base` | Whisper model size for `local_whisper`. |
| `whisper_probe_on_startup` | `true` | Probe for a working Whisper install at startup. |
| `openai_api_key` | `${OPENAI_API_KEY:-}` | API key for the OpenAI provider. |
| `azure_key` | `${AZURE_SPEECH_KEY:-}` | API key for the Azure Speech provider. |
| `max_audio_minutes` | `120` | Maximum audio length accepted for transcription. Calls longer than this are skipped with a warning (`src/core/artifacts/transcription/worker.py:1`). |

#### Transcription framework

The `transcription/` subpackage is documented in `src/core/artifacts/transcription/README.md`:

- **Providers** (`provider.py`) — `LocalWhisperProvider` (in-process Whisper, lazy model load), `OpenAIWhisperProvider` (OpenAI API), `AzureSpeechProvider` (Azure Speech SDK or REST batch). Each returns a `TranscriptionResult` with timestamped, optionally speaker-attributed segments. `get_transcription_provider(config)` selects the backend and raises `ValueError` on inconsistent config, which surfaces as the `misconfigured` capability state.
- **Worker** (`worker.py`) — `transcribe_call(call_id, db, config)` loads the `voice_calls` row, enforces gating (`enabled` + `auto_transcribe` + capability `AVAILABLE` + recorded + consent), resolves the recording reference from the linked `voice_call` artifact `payload["recording_ref"]`, runs the provider, creates a `TRANSCRIPT` artifact, links it with `set_transcript`, and emits `ARTIFACT_UPDATE`. `schedule_transcribe_call` is the fire-and-forget scheduler called from the voice lifecycle hook when a call ends.
- **Consent** — When `consent_required` is `true` (the default), a call must have at least one consented participant or transcription is skipped.

### Retention

| Key | Default | Description |
| --- | --- | --- |
| `run_cleanup_interval_minutes` | `60` | How often the retention cleanup task runs. |
| `purge_expired` | `true` | Whether to delete artifacts and transcripts past their `expires_at`. When `false`, the job only applies retention windows and never deletes. |
| `suite_deleted_retention_days` | `30` | Retention for deleted Suite content. |

#### Cleanup job

`RetentionCleanupJob` (`src/core/artifacts/retention.py:1`) is a daemon-thread worker started at boot. It runs only when `run_cleanup_interval_minutes > 0`. Each cycle calls `run_once(db)`, which:

1. Applies retention windows — sets `expires_at = created_at + retention_days` for any artifact that has a `retention_policy` but no `expires_at` yet.
2. Purges expired rows when `purge_expired` is `true`.

You can trigger an on-demand purge with `POST /api/v1/admin/artifacts/retention/purge`.

#### Per-server override and never-expire default

Retention is resolved with this priority:

1. The server's `retention_days` row in `server_artifact_settings` (only when `allow_per_server_override` is `true` and a row exists; created by migration `048_add_server_artifact_settings.py`).
2. The global `default_retention_days`. `null` means never expire.

`ArtifactManager.create` already applies this resolution when an artifact is first written, so the scheduled job keeps stored rows consistent with the active policy. `compute_expires_at(retention_days, created_at)` returns `None` when `retention_days` is `None` or not positive.

## Database schema

The tables are created by migration `047_add_artifacts_tables.py` (`src/core/artifacts/schema.py:1`).

- **`artifacts`** — Central record for every artifact. Key columns: `id` (Snowflake INTEGER PK), `conversation_id`, `channel_id`, `server_id`, `author_id`, `artifact_type` (`voice_call`, `whiteboard`, `upload`, `file`, `transcript`, `future`), `title`, `summary`, `status` (`live`, `completed`, `archived`), `recorded` (0/1), `has_transcript` (0/1), `payload` (JSON TEXT), `retention_policy`, `expires_at`, `license_feature`, `created_at`, `updated_at`. Indexed on `conversation_id`, `server_id`, `author_id`, `artifact_type`, `created_at`.
- **`voice_calls`** — Call metadata linked to `artifacts` by `artifact_id`. Columns include `initiator_id`, `started_at`, `ended_at`, `duration_seconds`, `recorded` (0/1), `transcript_artifact_id`, `consented_participants` (JSON TEXT), `participant_count`. Indexed on `artifact_id`, `conversation_id`, `server_id`.
- **`artifact_ops`** — Ordered, append-only operation log for collaborative artifacts. Columns: `id` (AUTOINCREMENT), `artifact_id`, `seq`, `op_type`, `actor_id`, `data` (JSON TEXT), `created_at`, with `UNIQUE(artifact_id, seq)` and indexes on `artifact_id` and `(artifact_id, seq)`.

See `src/core/artifacts/README.md` for the full reference.

## Capabilities

Several artifacts features can be unavailable for different reasons. The capability service (`src/core/artifacts/capabilities.py:1`) resolves each feature to a single availability state.

| Feature | Note |
| --- | --- |
| `artifacts` | Master switch. |
| `plexiscribe` | Requires the `plexiscribe` license feature. |
| `plexiscript` | Requires the `plexiscript` license feature. |
| `plexiboard` | Collaborative whiteboard, requires `artifacts_whiteboard`. |
| `artifacts_whiteboard` | Requires the `artifacts_whiteboard` license feature. |
| `voice_transcription` | Requires `voice_transcription`; provider-dependent. |
| `voice_recording` | Whether voice calls can be recorded. |

Each feature resolves to one `CapabilityState`:

- `available` — Usable.
- `disabled_by_config` — Turned off in config.
- `disabled_by_license` — Required license feature is missing.
- `dependency_missing` — A runtime dependency (for example, Whisper) is not installed.
- `misconfigured` — Enabled and licensed, but required configuration (for example, an API key) is missing or the provider is unknown.

### Endpoints

- `GET /api/v1/capabilities` (authentication required) — Returns `{feature: {state, message, details}}` for client banners.
- `GET /api/v1/admin/capabilities` (admin only) — Returns the same breakdown plus a top-level `summary` with counts and a `by_state` grouping for the admin dashboard.

## REST API

The artifact API is mounted under `/api/v1/artifacts` (user scope) and `/api/v1/admin/artifacts` (admin only). All user endpoints require a valid auth token.

### User endpoints (`/api/v1/artifacts`)

| Method | Path | Permission | Purpose |
| --- | --- | --- | --- |
| `POST` | `/artifacts` | `artifact.create` | Create an artifact and emit a transcript message. |
| `GET` | `/artifacts` | `artifact.view` | List artifacts with filters (type, status, author, search, paging). |
| `GET` | `/artifacts/{artifact_id}` | `artifact.view` | Get one artifact; `404` when missing. |
| `PATCH` | `/artifacts/{artifact_id}` | `artifact.edit` (or author) | Update mutable fields; `404` when missing. |
| `DELETE` | `/artifacts/{artifact_id}` | `artifact.delete` (or author) | Delete an artifact; `404` when missing. |
| `POST` | `/artifacts/convert-upload` | `artifact.create` | Convert an existing `msg_attachments` row into an `UPLOAD` artifact; `404` when the attachment is missing. |

For server-scoped actions, permission checks use the server RBAC layer. For DM and group conversations (no server), you must be a participant or owner of the conversation. The inline transcript message uses the `artifact` message type and references the artifact in `metadata.artifact_id`.

### Admin endpoints (`/api/v1/admin/artifacts`)

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/artifacts` | List all artifacts across every server. |
| `DELETE` | `/artifacts/{artifact_id}` | Force-delete any artifact. |
| `POST` | `/artifacts/retention/purge` | Delete artifacts whose `expires_at` is in the past. Returns `{purged: <count>}`. |

### Permission names

- `artifact.view`
- `artifact.create`
- `artifact.edit`
- `artifact.delete`
- `artifact.manage_retention` (reserved; the admin purge endpoint is admin-guarded instead)

## Core manager

Domain logic lives in `src/core/artifacts/` and does not depend on routes, WebSocket handlers, or voice-call lifecycle code.

- **`models.py`** — Dataclasses `Artifact` and `VoiceCall`, and enums `ArtifactType` (`voice_call`, `whiteboard`, `upload`, `file`, `transcript`, `future`) and `ArtifactStatus` (`live`, `completed`, `archived`). These mirror the table columns; boolean DB columns are exposed as `bool` and JSON columns as `dict` or `list`.
- **`repository.py`** — Data-access functions (`create_artifact`, `get_artifact`, `update_artifact`, `delete_artifact`, `list_artifacts`, `count_artifacts`) plus `row_to_artifact` and `artifact_to_row`. Sort keys are restricted to an allow-list (`created_at`, `title`, `type`, `duration`) so user input is never interpolated into SQL. `list_artifacts` defaults to a limit of 200 (`_DEFAULT_LIST_LIMIT`) when no limit is provided.
- **`manager.py`** — `ArtifactManager(BaseManager)` wraps the repository and the `artifacts` config block. Public methods: `create`, `get`, `update`, `delete`, `list_with_filters`, `count`, `convert_upload_to_artifact`.

### Upload conversion

`convert_upload_to_artifact(attachment, conversation_id, author_id, ...)` creates an `UPLOAD` or `FILE` artifact that references an existing attachment (media is not re-uploaded). The `attachment` dict must include `attachment_id` (or `id`); `filename`, `content_type`, `size`, `url`, and `metadata` are copied into `payload`. The `size` field is coerced safely — unparseable values become `0` — so a string size cannot cause a 500.

### Voice-call artifacts

When a voice call ends, it produces a `voice_calls` record and a `voice_call` artifact, so the call appears in chat history and the artifacts pane. The lifecycle is driven by channel occupancy: the call starts when the first participant joins and ends when the last leaves.

- **Start** — `VoiceCallManager.start_call(channel_id, server_id, initiator_id, conversation_id=None)` inserts a `voice_calls` row (`started_at` is now, `participant_count` is 1) and creates a linked `voice_call` artifact in `LIVE` status. The `voice_calls.id` is stored in the artifact `payload`.
- **End** — `VoiceCallManager.end_call(call_id, participant_ids=None)` sets `ended_at`, `duration_seconds`, and `participant_count`, then transitions the linked artifact to `COMPLETED`.
- **Recording** — `config.artifacts.voice.allow_recording` gates recording. When `false`, `mark_recorded(call_id, True)` is clamped to `false` and the artifact `recorded` flag is never set. The flag is mirrored on both the `voice_calls` row and the linked artifact.
- **Consent** — `add_consent(call_id, user_id)` appends to the `consented_participants` JSON list (deduped) on the `voice_calls` row.
- **Transcript** — `set_transcript(call_id, transcript_artifact_id)` links a transcript artifact and sets `has_transcript = true` on the linked artifact.

If the artifacts layer is unavailable, the voice pipeline continues and simply omits the call record.

## Real-time fabric (WebSocket)

Collaborative artifacts (live whiteboards, shared code editors, and similar canvases) sync through the WebSocket gateway. REST routes remain the source of truth for persisted state; the fabric only relays in-memory deltas and snapshots.

### Gateway opcodes (60–63)

Defined in `src/api/websocket/opcodes.GatewayOpcode:1`:

| Opcode | Name | Direction | Purpose |
| --- | --- | --- | --- |
| 60 | `ARTIFACT_SUBSCRIBE` | Client to server | Subscribe to an artifact's live updates. |
| 61 | `ARTIFACT_UNSUBSCRIBE` | Client to server | Unsubscribe from an artifact. |
| 62 | `ARTIFACT_OP` | Client to server to clients | Relay a real-time delta operation to subscribers. |
| 63 | `ARTIFACT_SYNC` | Server to client | Full snapshot for late joiners. |

### Event types and intents

- `src/core/events/types.py:86` — `ARTIFACT_CREATE`, `ARTIFACT_UPDATE`, `ARTIFACT_DELETE`, `ARTIFACT_OP`.
- `src/core/events/types.py:120` — `GatewayIntent.ARTIFACTS = 1 << 20` gates delivery of those events and is part of both `default_intents()` and `all_intents()` (`src/core/events/types.py:124`). See [Gateway intents](../websocket/intents.md) for the full intent catalog.

### Relay helpers

`src/api/websocket/artifacts.py:1` owns the in-memory subscription registry (`ArtifactSubscriptionRegistry`: `subscribe`, `unsubscribe`, `get_subscribers`, `unsubscribe_all`) and two helpers used by the opcode handlers:

- `relay_artifact_op(dispatcher, artifact_id, op, actor_id, exclude_user_id)` — Fans an `ARTIFACT_OP` to every other subscriber of an artifact. This function only relays; persistence is handled elsewhere.
- `send_artifact_sync(connection, artifact_id, snapshot)` — Sends an `ARTIFACT_SYNC` full snapshot to a single connection (late-joiner bootstrap).

Both reuse the existing dispatcher send path and the per-connection rate limit.

## Federation (PlexiJoin)

When two Plexichat instances are federated through PlexiJoin, artifacts owned by a federated server can share live operations and artifact-level events across the link. The integration is at the manager level in `src/core/artifacts/federation.py:1` (`FederationArtifactBridge`) and does not build a cross-instance real-time transport (that layer lives outside this repo).

- **Server-to-link resolution** — The bridge resolves an artifact's `server_id` and maps it to active federation connections where `plexijoin_connections.remote_instance_id == server_id`.
- **Operation forwarding** — `relay_artifact_op` in `src/api/websocket/artifacts.py:1` calls `bridge.forward_artifact_op(artifact_id, op, actor_id)` immediately after the local fan-out. The call is wrapped in `try/except` so federation failures never break local relay.
- **Event forwarding** — `forward_artifact_event(event_type, artifact)` shares artifact create, update, and delete events to matching links.
- **Traffic accounting** — Every forwarded operation and event is recorded with `PlexiJoinManager.record_traffic`.
- **Visibility** — `get_federated_artifact_visibility(server_id)` returns whether artifacts on that server can be shared cross-instance (at least one active link exists).

The cross-instance transport is set with `set_federation_transport(fn)`. The default transport records traffic and logs the forwarding intent; a deployment replaces it with a function that performs the network send when the external transport is available.

The initializer builds `FederationArtifactBridge(db, plexijoin_manager)` only when PlexiJoin is licensed and its encryption service is available, then attaches it with `set_artifact_federation_bridge(bridge)`.

The bridge only forwards operations and events. It never grants local permissions to remote participants — visibility of federated artifacts remains gated by the local route and permission layer.

## DSAR and privacy

Artifacts, voice-call records, and transcripts are personal data and are covered by DSAR (GDPR data-portability and erasure) flows.

### Export

The DSAR collector (`src/core/dsar/collector.py:1`) includes, in the `voice` category:

- The user's `voice_states`.
- `voice_calls` that the user initiated or consented to (`consented_participants`).
- The `voice_call` and `transcript` artifacts that the user authored.

Transcript text is included inline as `transcript_text` (extracted from `payload` JSON) so the export does not require re-parsing segment arrays. The DSAR preview counts expose `content_artifacts`, `content_transcripts`, and `voice_voice_calls`. Every export format (JSON and ZIP) serializes the collected dict, so transcripts and artifact metadata are always present in the download.

### Deletion and anonymization

On account purge, `anonymize_user_artifacts(db, user_id, config)` (`src/core/artifacts/privacy.py:1`), called from `AccountReaper.purge_user`, applies the same `anonymize_content` policy used for messages:

- `anonymize_content=True` (default) — Rows are kept but identifiable links are removed: `author_id` becomes the sentinel anonymized ID, inline transcript text is stripped from `payload`, the user is removed from `consented_participants` on calls they only consented to, and `artifact_ops` rows where `actor_id` matches the user are deleted. Calls they initiated are anonymized (`initiator_id` becomes the sentinel) rather than removed.
- `anonymize_content=False` — Artifacts the user authored, their linked `artifact_ops`, and `voice_calls` rows they initiated are deleted; consented-participant entries are removed from calls they did not start.

The function returns the number of rows touched for auditing.

## Admin panel

The server-rendered admin dashboard exposes an **Artifacts** tab (`data-tab="artifacts"`) for oversight and maintenance.

### Capability banners

`renderArtifactCapabilities()` calls `GET /api/v1/admin/capabilities` and renders one banner per feature (`artifacts`, `plexiscribe`, `plexiscript`, `plexiboard`, `artifacts_whiteboard`, `voice_transcription`, `voice_recording`). Each banner shows the feature name, its `state`, and the backend `message` (plus `details` when present).

| State | Banner color | Meaning |
| --- | --- | --- |
| `available` | Green | Feature is usable. |
| `disabled_by_config` | Amber | Turned off in config. |
| `disabled_by_license` | Red | Required license feature is missing. |
| `dependency_missing` | Red | A runtime dependency (for example, Whisper) is not installed. |
| `misconfigured` | Red | Enabled and licensed, but required config (for example, an API key) is missing. |

### Artifact list and force-delete

`renderArtifactList()` calls `GET /api/v1/admin/artifacts` and renders a table (type, title, author, server, created, retention, expires). Each row has a **Force delete** button that calls `DELETE /api/v1/admin/artifacts/{id}` after confirmation and re-renders the table. A **Refresh** button re-fetches capabilities and the list.

### Retention controls

- **Run retention purge** — `runRetentionPurge()` posts to `POST /api/v1/admin/artifacts/retention/purge` and shows the `{purged: <count>}` result in a toast.
- **Per-server override** — `saveServerRetention()` posts `{server_id, retention_days}` to `POST /api/v1/admin/artifacts/retention/server`. A blank `retention_days` clears the override so the server reverts to the global `default_retention_days`.

## Environment variables

The deploy scripts (`deploy.sh` and `deploy.ps1`) support environment-variable overrides for the `artifacts.*` config block.

### Artifact config overrides

| Variable | Config key | Notes |
| --- | --- | --- |
| `ARTIFACTS_ENABLED` | `artifacts.enabled` | `true` or `false` |
| `ARTIFACTS_PLEXISCRIBE_ENABLED` | `artifacts.plexiscribe.enabled` | `true` or `false` |
| `ARTIFACTS_PLEXISCRIPT_ENABLED` | `artifacts.plexiscript.enabled` | `true` or `false` |
| `ARTIFACTS_WHITEBOARD_ENABLED` | `artifacts.whiteboard.enabled` | `true` or `false` |
| `ARTIFACTS_VOICE_ALLOW_RECORDING` | `artifacts.voice.allow_recording` | `true` or `false` |
| `ARTIFACTS_VOICE_TRANSCRIPTION_ENABLED` | `artifacts.voice.transcription.enabled` | `true` or `false` |
| `ARTIFACTS_VOICE_TRANSCRIPTION_PROVIDER` | `artifacts.voice.transcription.provider` | `local_whisper`, `openai`, or `azure` |
| `ARTIFACTS_DEFAULT_RETENTION_DAYS` | `artifacts.default_retention_days` | Positive integer or empty (never expire) |

### License feature flags

| Variable | Purpose |
| --- | --- |
| `ARTIFACTS_LICENSE_FEATURES` | Comma-separated list of active license features. Valid values: `artifacts`, `artifacts_whiteboard`, `voice_transcription`, `voice_recording`, `plexiscribe`, `plexiscript`. |

Example (bash):

```bash
ARTIFACTS_WHITEBOARD_ENABLED=true \
ARTIFACTS_VOICE_TRANSCRIPTION_ENABLED=true \
ARTIFACTS_VOICE_TRANSCRIPTION_PROVIDER=openai \
ARTIFACTS_LICENSE_FEATURES=artifacts,artifacts_whiteboard,voice_transcription \
./deploy.sh install -r
```

Example (PowerShell):

```powershell
$env:ARTIFACTS_WHITEBOARD_ENABLED = "true"
$env:ARTIFACTS_VOICE_TRANSCRIPTION_ENABLED = "true"
$env:ARTIFACTS_LICENSE_FEATURES = "artifacts,artifacts_whiteboard,voice_transcription"
.\deploy.ps1 install -RunNow
```

When a variable is unset or empty, the server uses the compiled-in default from `src/config_defaults_artifacts.py:12` and `src/config_defaults.py:36`.

## Related documentation

- [Default configuration reference](../default-config.md) — Complete configuration reference.
- [Feature overview](../features.md) — All Plexichat features.
