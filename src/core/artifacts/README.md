# Artifacts

This document describes the artifacts module for Plexichat. Use it to persist, query, and manage durable outputs of a conversation, such as voice calls, whiteboards, uploads, and transcripts.

Artifacts are first-class records. This module owns the database schema, domain models, repository, and manager. Routes, WebSocket handlers, and voice-call-specific wiring are provided by other modules.

## Overview

| File | Responsibility |
| --- | --- |
| `schema.py` | Defines `create_tables(db)` for the three tables below. Uses `split_sql_statements` and `db.convert_schema` when available, and executes with `CREATE TABLE IF NOT EXISTS` so creation is idempotent. Migration `047_add_artifacts_tables.py` calls `create_tables` in `up` and drops tables in `down`. |
| `models.py` | Dataclasses `Artifact` and `VoiceCall`, plus enums `ArtifactType` and `ArtifactStatus`. |
| `repository.py` | Low-level database functions for creating, reading, and listing artifacts. |
| `manager.py` | `ArtifactManager` business logic, including retention handling and `convert_upload_to_artifact`. |
| `voice_calls.py` | `VoiceCallManager` lifecycle for voice calls. |
| `capabilities.py` | Computes availability of each artifact feature. |
| `retention.py` | `purge_expired` and `RetentionCleanupJob` background worker. |
| `federation.py` | `FederationArtifactBridge` for PlexiJoin. |
| `privacy.py` | `anonymize_user_artifacts` for erasure. |

## ID convention

All identifiers use Snowflake integers and are stored as `INTEGER`. The exception is `artifact_ops.id`, which is an auto-incrementing local row ID (`INTEGER PRIMARY KEY AUTOINCREMENT`). Ordering within an artifact uses the `seq` column.

## Schema

### `artifacts`

The central record for every artifact.

| Column | Type | Notes |
| --- | --- | --- |
| `id` | INTEGER | Snowflake primary key. |
| `conversation_id` | INTEGER | Nullable. Owning conversation. |
| `channel_id` | INTEGER | Nullable. Owning channel. |
| `server_id` | INTEGER | Nullable. Owning server. |
| `author_id` | INTEGER | NOT NULL. Creator. |
| `artifact_type` | TEXT | NOT NULL. One of `voice_call`, `whiteboard`, `upload`, `file`, `transcript`, `future`. |
| `title` | TEXT | NOT NULL. Display title. |
| `summary` | TEXT | Nullable. Short description. |
| `status` | TEXT | NOT NULL, default `completed`. One of `live`, `completed`, `archived`. |
| `recorded` | INTEGER | NOT NULL, default `0`. Boolean flag. |
| `has_transcript` | INTEGER | NOT NULL, default `0`. Boolean flag. |
| `payload` | TEXT | Nullable. JSON-encoded type-specific data. |
| `retention_policy` | TEXT | Nullable. Per-artifact retention override. |
| `expires_at` | INTEGER | Nullable. Expiry timestamp in milliseconds. `NULL` means never expire. |
| `license_feature` | TEXT | Nullable. Feature flag, for example `artifacts_whiteboard`. |
| `created_at` | INTEGER | NOT NULL. Creation timestamp. |
| `updated_at` | INTEGER | NOT NULL. Last-update timestamp. |

Indexes: `conversation_id`, `server_id`, `author_id`, `artifact_type`, `created_at`.

### `voice_calls`

Call-specific metadata. A completed call typically creates an `artifacts` row with `artifact_type = voice_call`.

| Column | Type | Notes |
| --- | --- | --- |
| `id` | INTEGER | Snowflake primary key. |
| `artifact_id` | INTEGER | Nullable. References `artifacts.id`. |
| `conversation_id` | INTEGER | Nullable. |
| `channel_id` | INTEGER | Nullable. |
| `server_id` | INTEGER | Nullable. |
| `initiator_id` | INTEGER | User who started the call. |
| `started_at` | INTEGER | NOT NULL. Call start timestamp. |
| `ended_at` | INTEGER | Nullable. |
| `duration_seconds` | INTEGER | Nullable. |
| `recorded` | INTEGER | NOT NULL, default `0`. Boolean. |
| `transcript_artifact_id` | INTEGER | Nullable. Linked transcript artifact. |
| `consented_participants` | TEXT | Nullable. JSON list of user IDs that consented to recording. |
| `participant_count` | INTEGER | Default `0`. |
| `created_at` | INTEGER | NOT NULL. |
| `updated_at` | INTEGER | NOT NULL. |

Indexes: `artifact_id`, `conversation_id`, `server_id`.

### `artifact_ops`

Append-only operations log for collaborative artifacts such as whiteboards. Operations are ordered per artifact by `seq`.

| Column | Type | Notes |
| --- | --- | --- |
| `id` | INTEGER | Auto-incrementing row ID. |
| `artifact_id` | INTEGER | NOT NULL. Owning artifact. |
| `seq` | INTEGER | NOT NULL. Monotonic per artifact. |
| `op_type` | TEXT | NOT NULL. For example `stroke`, `edit`, `cursor`, `snapshot`. PlexiScribe persists `text`, `ins_block`, `del_block`, `set_type`, `set_style`, `set_marks`, `replace_all`; `presence`, `presence_leave`, `cursor`, `typing`, `selection` are transient. |
| `actor_id` | INTEGER | Nullable. Producer. |
| `data` | TEXT | NOT NULL. JSON-encoded payload. |
| `created_at` | INTEGER | NOT NULL. |

Constraint: `UNIQUE(artifact_id, seq)`. Indexes: `artifact_id`, `(artifact_id, seq)`.

## Models

Defined in `models.py` as pure dataclasses. Boolean columns are `int` in the database and `bool` on the model.

*   `ArtifactType` — `VOICE_CALL`, `WHITEBOARD`, `UPLOAD`, `FILE`, `TRANSCRIPT`, `FUTURE`, `PLEXISCRIBE`, `PLEXISCRIPT`, `SUM`. Unknown persisted values map to `FUTURE`.
*   `ArtifactStatus` — `LIVE`, `COMPLETED`, `ARCHIVED`.
*   `Artifact` — Fields: `id`, `conversation_id`, `channel_id`, `server_id`, `author_id`, `artifact_type`, `title`, `summary`, `status`, `recorded`, `has_transcript`, `payload` (`dict`), `created_at`, `updated_at`, `retention_policy`, `expires_at`, `license_feature`.
*   `VoiceCall` — Fields: `id`, `artifact_id`, `conversation_id`, `channel_id`, `server_id`, `initiator_id`, `started_at`, `ended_at`, `duration_seconds`, `recorded`, `transcript_artifact_id`, `consented_participants` (`list[int]`), `participant_count`, `created_at`, `updated_at`.

## Repository

`repository.py` operates on a `db` connection with parameterized SQL. Sort keys are restricted to an allow-list.

| Function | Description |
| --- | --- |
| `create_artifact(db, artifact)` | Inserts the artifact and returns it. |
| `get_artifact(db, artifact_id)` | Returns an `Artifact` or `None`. |
| `update_artifact(db, artifact_id, **fields)` | Updates only known columns. Normalizes enums, booleans, and JSON. |
| `delete_artifact(db, artifact_id)` | Deletes the row. Returns `True` if a row was removed. |
| `list_artifacts(db, filters)` | Lists artifacts. See filters below. |
| `count_artifacts(db, filters)` | Counts artifacts matching filters. |
| `row_to_artifact(row)` | Converts a database row to `Artifact`. |
| `artifact_to_row(artifact)` | Converts an `Artifact` to a database row. |

`list_artifacts` supports: `conversation_id`, `channel_id`, `server_id`, `author_id`, `artifact_type` (single value or list), `status`, `recorded` (`bool`), `has_transcript` (`bool`), `search` (`LIKE` on title and summary), `sort_by` (`created_at`, `title`, `type`, `duration` where `duration` joins `voice_calls`), `sort_order` (`asc` or `desc`), `limit`, `offset`.

## Manager

`ArtifactManager(BaseManager)` wraps the repository and the `artifacts` config.

```python
from src.core.artifacts.manager import ArtifactManager

manager = ArtifactManager(db, config=None)

artifact = manager.create(
    conversation_id=123,
    author_id=456,
    artifact_type="upload",
    title="Design doc",
)
fetched = manager.get(artifact.id)
results = manager.list_with_filters({"artifact_type": "voice_call"}, server_id=1)
count = manager.count({"author_id": 456})
manager.delete(artifact.id)
```

Key behaviors:

*   `create(...)` — Generates a Snowflake ID, computes `expires_at` from the resolved retention period, and persists the artifact.
*   `list_with_filters(filters, conversation_id, server_id, channel_id, author_id)` — Merges scope arguments into filters and delegates to `list_artifacts`. The route layer enforces visibility and permissions.
*   `delete(artifact_id)` — Deletes the metadata row only. Cascade cleanup of `voice_calls` and `artifact_ops` is deferred to later groups.
*   `convert_upload_to_artifact(attachment, conversation_id, author_id, title, artifact_type, server_id, channel_id)` — Creates an `UPLOAD` or `FILE` artifact from an existing attachment. The `attachment` dict must contain `attachment_id` or `id`; `filename`, `content_type`, `size`, `url`, and `metadata` are copied into `payload`. It queries `msg_attachments` with `deleted = 0`.

### Retention logic

`compute_expires_at(retention_days, created_at)` returns an expiry timestamp in milliseconds, or `None` if `retention_days` is `None` or not positive. `_resolve_retention_days` resolves the effective period in priority order:

1.  Per-artifact `retention_policy` containing `days`.
2.  Per-server override when `allow_per_server_override` is `True` and `artifacts.servers.<server_id>.retention_days` exists.
3.  Global `default_retention_days` (`None` means never expire).

## Voice call lifecycle

`VoiceCallManager(db, artifact_manager, config)` mediates between `voice_calls` and the linked `voice_call` artifact. The voice module wraps all calls so a failure in the artifacts layer does not break voice.

| Method | Description |
| --- | --- |
| `start_call(channel_id, server_id, initiator_id, conversation_id)` | Inserts a `voice_calls` row (`started_at = now`, `participant_count = 1`) and creates a linked artifact with `status = LIVE` and `recorded` from `artifacts.voice.allow_recording`. |
| `end_call(call_id, participant_ids)` | Sets `ended_at`, `duration_seconds`, and `participant_count`, and transitions the artifact to `COMPLETED`. |
| `mark_recorded(call_id, recorded)` | Sets the `recorded` flag. Clamped to `False` when `allow_recording` is `False`. |
| `add_consent(call_id, user_id)` | Appends a user to `consented_participants` (deduped JSON list). |
| `set_transcript(call_id, transcript_artifact_id)` | Links a transcript artifact and sets `has_transcript = True` on the source artifact. |
| `get_active_by_channel(channel_id)` | Returns the active call for a channel or `None`. |

## Capability service

`capabilities.py` computes the availability of each feature for the admin panel and clients.

### States

Each feature resolves to one `CapabilityState`:

| State | Meaning |
| --- | --- |
| `available` | The feature is usable. |
| `disabled_by_config` | Disabled via config. |
| `disabled_by_license` | Disabled because the required license feature is absent. |
| `dependency_missing` | A runtime dependency is not installed. |
| `misconfigured` | Enabled and licensed, but required configuration is missing or the provider is unknown. |

`CapabilityInfo` contains `feature`, `state`, `message`, and optional `details`.

### Evaluated features

`get_artifact_capabilities(config)` returns a dict keyed by feature name:

*   `artifacts` — `disabled_by_config` when `artifacts.enabled is False`, else `available`.
*   `plexiscribe` — `disabled_by_config` when `artifacts.enabled` or `artifacts.plexiscribe.enabled` is `False`; `disabled_by_license` when `has_feature("plexiscribe")` is `False`.
*   `plexiscript` — Same gating as `plexiscribe` with its own config and license flag.
*   `artifacts_whiteboard` — `disabled_by_config` when `artifacts.enabled` or `artifacts.whiteboard.enabled` is `False`; `disabled_by_license` when `has_feature("artifacts_whiteboard")` is `False`.
*   `voice_transcription` — `disabled_by_config` when `artifacts.enabled` or `artifacts.voice.transcription.enabled` is `False`; `disabled_by_license` when `has_feature("voice_transcription")` is `False`; otherwise delegated to `get_transcription_provider(config).is_available()`:
    *   `local_whisper` — `dependency_missing` if Whisper is not importable, else `available`.
    *   `openai` or `azure` — `misconfigured` if the API key is empty, else `available`.
    *   Unknown provider — `misconfigured`.
*   `voice_recording` — `disabled_by_config` when `artifacts.voice.allow_recording is False`, else `available`.

Both `get_artifact_capabilities` and `get_capability(feature, config)` accept an optional config dict and never raise.

### API endpoints

*   `GET /api/v1/capabilities` — Authenticated, user scope. Returns `{feature: {state, message, details}}`.
*   `GET /api/v1/admin/capabilities` — Admin-guarded. Returns the same per-feature breakdown plus a top-level `summary` with counts grouped by `by_state`.

## REST API

User routes live in `src/api/routes/artifacts.py` under `/api/v1/artifacts`. Admin routes live in `src/api/routes/admin/artifacts.py` under `/api/v1/admin/artifacts`.

### User endpoints

| Method | Path | Permission | Description |
| --- | --- | --- | --- |
| `POST` | `/artifacts` | `artifact.create` | Create an artifact and emit a transcript message. |
| `GET` | `/artifacts` | `artifact.view` | List artifacts with filters. |
| `GET` | `/artifacts/{artifact_id}` | `artifact.view` | Fetch one artifact. Returns `404` if missing. |
| `PATCH` | `/artifacts/{artifact_id}` | `artifact.edit` or author | Update mutable fields. Returns `404` if missing. |
| `DELETE` | `/artifacts/{artifact_id}` | `artifact.delete` or author | Delete an artifact. Returns `404` if missing. |
| `POST` | `/artifacts/convert-upload` | `artifact.create` | Convert an existing `msg_attachments` row to an `UPLOAD` artifact. Returns `404` if the attachment is missing. |

After create or convert, the route emits `MESSAGE_CREATE` via `events.create_message_create` with `data["type"] = 0`, `data["message_type"] = "artifact"`, and `data["metadata"] = {"artifact_id": ...}`.

### Admin endpoints

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/artifacts` | List all artifacts across servers. |
| `DELETE` | `/artifacts/{artifact_id}` | Force-delete any artifact. |
| `POST` | `/artifacts/retention/purge` | Call `purge_expired` and return `{purged: <count>}`. |

Permission names resolved through server RBAC: `artifact.view`, `artifact.create`, `artifact.edit`, `artifact.delete`, `artifact.manage_retention`.

## Retention purge

`purge_expired(db, config)` deletes rows where `expires_at IS NOT NULL AND expires_at <= now` and returns the count. `RetentionCleanupJob(db, config)` is a daemon thread started by `src/server/initializer.py` and stored in `modules_store["artifact_retention"]`. It runs only when `artifacts.retention.run_cleanup_interval_minutes > 0`.

Each cycle runs:

1.  `_apply_retention_windows(db, config)` — For every artifact with a `retention_policy` but `NULL` `expires_at`, sets `expires_at = created_at + resolved_days`.
2.  `purge_expired(db, config)` — Deletes expired rows. Skipped when `artifacts.retention.purge_expired` is `False`.

Use `resolve_retention_days(server_id, config)` to get the effective period for a server. It returns `None` for never expire. Priority:

1.  Per-server row in `server_artifact_settings.retention_days` when `allow_per_server_override` is `True`.
2.  Global `artifacts.default_retention_days`.

When `default_retention_days` is `None`, artifacts never expire unless a per-artifact policy or server override sets a positive value.

## Real-time fabric

The WebSocket gateway provides live collaboration for whiteboards and editors. It reuses the dispatcher send path and per-connection rate limits.

*   Gateway opcodes (`src/api/websocket/opcodes.GatewayOpcode`) — `ARTIFACT_SUBSCRIBE = 60`, `ARTIFACT_UNSUBSCRIBE = 61`, `ARTIFACT_OP = 62`, `ARTIFACT_SYNC = 63`.
*   Event types (`src/core/events/types.EventType`) — `ARTIFACT_CREATE`, `ARTIFACT_UPDATE`, `ARTIFACT_DELETE`, `ARTIFACT_OP`.
*   Gateway intent — `GatewayIntent.ARTIFACTS = 1 << 20`, included in `default_intents()` and `all_intents()`.
*   Helpers in `src/api/websocket/artifacts.py`:
    *   `relay_artifact_op(dispatcher, artifact_id, op, actor_id, exclude_user_id, seq)` — Fans an `ARTIFACT_OP` to other subscribers. Persists are handled by the opcode handler. `seq` is carried in `d.seq` for ordering.
    *   `send_artifact_ack(connection, artifact_id, op, seq)` — Echoes a persisted op to its actor as an acknowledgement.
    *   `send_artifact_sync(connection, artifact_id, snapshot)` — Sends a full snapshot to a late joiner.

## Federation

`src/core/artifacts/federation.py` defines `FederationArtifactBridge(db, plexijoin_manager)`, which shares live operations and artifact events across federated instances without assuming a cross-instance transport.

*   `forward_artifact_op(artifact_id, op, actor_id)` — Forwards a live delta to active federation connections for the artifact's server.
*   `forward_artifact_event(event_type, artifact)` — Forwards create, update, or delete events.
*   `get_federated_artifact_visibility(server_id)` — Returns `True` if at least one active federation link exists for the server.

Both forward methods record traffic via `PlexiJoinManager.record_traffic` and invoke an injectable transport set by `set_federation_transport(fn)`. The default transport logs the intent; a deployment replaces it with a real network send. The bridge only forwards. It does not grant permissions to remote participants.

`relay_artifact_op` calls `bridge.forward_artifact_op` after local fan-out. Federation errors are caught and do not break local relay.

## Privacy

Artifacts are personal data. Two flows handle portability and erasure:

*   Export — `src/core/dsar/collector.py` `_collect_voice` returns `voice_states`, `voice_calls` where the user is initiator or consented participant, and `voice_call` and `transcript` artifacts authored by the user. Transcript text is extracted to `transcript_text` for human-readable export.
*   Erasure — `anonymize_user_artifacts(db, user_id, config)` in `src/core/artifacts/privacy.py`, called from `AccountReaper.purge_user`:
    *   `anonymize_content = True` — Replace `author_id` with a sentinel, strip inline transcript text from `payload`, remove the user from `consented_participants`, and delete their `actor_id` rows in `artifact_ops`.
    *   `anonymize_content = False` — Delete authored artifacts, their `artifact_ops`, and `voice_calls` initiated by the user; remove consented entries otherwise.

## Related code

*   `plexichat/src/core/artifacts/schema.py` — `create_tables`
*   `plexichat/src/core/artifacts/models.py:17` — `ArtifactType`
*   `plexichat/src/core/artifacts/repository.py` — `list_artifacts`, `row_to_artifact`
*   `plexichat/src/core/artifacts/manager.py` — `ArtifactManager`
*   `plexichat/src/core/artifacts/voice_calls.py` — `VoiceCallManager`
*   `plexichat/src/core/artifacts/capabilities.py` — `get_artifact_capabilities`
*   `plexichat/src/core/artifacts/retention.py` — `purge_expired`, `RetentionCleanupJob`
*   `plexichat/src/core/artifacts/federation.py` — `FederationArtifactBridge`
