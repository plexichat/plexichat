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

### `retention`

| Key | Default | Meaning |
| --- | --- | --- |
| `run_cleanup_interval_minutes` | `60` | How often the retention cleanup task runs. |
| `purge_expired` | `true` | Delete artifacts/transcripts past their retention window. |

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

## Related Documentation

- [Default Configuration Reference](../default-config.md) - Complete configuration reference
- [Feature Overview](../features.md) - All Plexichat features
