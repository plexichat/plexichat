# Events, artifacts, and related hardening — batch findings

This document summarizes a batch of hardening fixes for events, artifacts, reports, automod, avatars, voice, and messaging. Each section lists what failed, how you can observe it, and what changed.

## Events layer

### Idle-TTL eviction and critical subscribers

**Problem:** The subscriber idle-TTL sweep only logged evictions at `DEBUG` and could remove the `_last_used` entry for a critical subscriber while leaving it registered. This caused cap-eviction and merged-pool bookkeeping to drift.

**Fix:** `_maybe_prune_locked` in `src/core/events/manager.py:1` now skips critical callbacks entirely, increments an `evicted_subscribers` counter exposed by `get_loss_metrics()`, and logs evictions at `WARNING`.

### Cross-worker echo protection dropped pre-identity events

**Problem:** Events published before `set_worker_id` are tagged `sw=unknown`. The consumer dropped any event whose tag matched the local worker ID, so on multi-worker deployments siblings discarded each other's `unknown` events.

**Fix:** The consumer in `src/api/websocket/__init__.py:1` now only skips a message when the source tag is non-empty, not `"unknown"`, and equals the local worker ID.

### Exception handling in the emission path

**Problem:** `gateway_emit._emit` (`src/core/events/gateway_emit.py:1`) logged dispatch failures at `DEBUG`, so serialization or routing errors were invisible at default log levels.

**Fix:** Failures now log at `WARNING` while remaining non-raising.

### Replay-buffer overflow

**Problem:** `Session.add_replay_event` (`src/api/websocket/session.py:1`) evicted the oldest buffered event without any signal.

**Fix:** The first overflow per session now logs a `WARNING` that describes the gap.

### Connection close and send lock

**Problem:** `Connection.close` (`src/api/websocket/connection.py:1`) could interleave a final frame after the close handshake when racing `send_json`.

**Fix:** `close()` now acquires `_send_lock` around the WebSocket close.

### Compression context after send failure

**Problem:** A partially failed compressed send left the shared zlib context mid-stream and corrupted every subsequent binary frame.

**Fix:** `send_json` rebuilds the `compressobj` on failure so the next frame starts from a clean boundary (`src/api/websocket/connection.py:1`).

### Dispatcher loop lookup and silent drop

**Problem:** The fallback in `src/api/websocket/dispatcher.py:1` used `asyncio.get_event_loop()` (deprecated in Python 3.12+) and silently dropped events when no loop was available.

**Fix:** The dispatcher now prefers the running loop, falls back to the captured per-connection loop, and logs a `WARNING` when an event cannot be dispatched.

### Voice speaking channel ID type mismatch

**Problem:** `handle_voice_speaking` (`src/api/websocket/handlers/voice.py:1`) compared an authoritative integer `voice_state.channel_id` with the client-supplied string, so speaking updates never dispatched.

**Fix:** The channel ID is now coerced and validated before comparison.

### Health endpoint events metrics

**Problem:** `src/api/routes/health.py:1` read `events_module.event_manager`, but only `_manager` existed, so advisory fields were always `None`.

**Fix:** `src/core/events/__init__.py:1` now exposes a PEP 562 `event_manager` alias, so `/health` reports real saturation metrics.

## Artifacts

### Transcription worker queue

**Problem:** The fallback scheduling queue was an unbounded `asyncio.Queue` created outside a running loop and written from arbitrary threads (`src/core/artifacts/transcription/worker.py:1`).

**Fix:** The queue is now a bounded (512) `queue.Queue` (thread-safe, no loop binding). The async drainer polls it, and a full queue drops the job with a `WARNING`.

### Multi-segment recordings

**Problem:** `_resolve_local_recording_path` rejected `call_<id>_track_<n>.*` files, so consent-interrupted segmented calls were never transcribed.

**Fix:** Track files are now accepted alongside the single-file `call_<id>.*` form.

### Audio duration cap not enforced

**Problem:** `max_audio_minutes` was documented but never enforced.

**Fix:** The worker now skips transcription with a `WARNING` when the call's recorded duration exceeds the cap.

### Diarizer discovery retry

**Problem:** A failed `_find_diarization_module()` or import was retried on every call (for example, probing `pyannote` with no Hugging Face token) (`src/core/artifacts/transcription/provider.py:1`).

**Fix:** Failures are memoized with `_diarization_failed`, so transcription continues with `unknown` speakers.

### Attachment size type

**Problem:** `convert_upload_to_artifact` (`src/core/artifacts/manager.py:1`) compared a possibly-string `size` with the byte cap, which raised `TypeError` and surfaced as 500.

**Fix:** The size is now coerced safely; unparseable values become `0`.

### Unbounded artifact listing

**Problem:** An omitted `limit` returned the entire `artifacts` table; only the HTTP route clamped.

**Fix:** The repository (`src/core/artifacts/repository.py:1`) now applies `_DEFAULT_LIST_LIMIT` (200) so internal callers cannot materialize unbounded result sets.

### Immutable columns in voice-call updates

**Problem:** `update_voice_call` allowed rewriting `id`, `artifact_id`, `created_at`, and `updated_at`.

**Fix:** Those columns are now always ignored by the updater.

## Reports

### Tuple-row timestamp fallback

**Problem:** Short tuple rows in `src/core/reports/__init__.py:1` fell back to index 11 or 8 (the priority column) for `reported_at`, which produced invalid timestamps.

**Fix:** Missing timestamps are now `0`.

### Reopening kept stale review metadata

**Problem:** Setting a report back to `open` left `resolved_at`, `resolved_by`, `reviewed_at`, and `reviewed_by` from the previous review (`src/api/routes/features/reports.py:1`).

**Fix:** The status-update SQL now clears all lifecycle metadata on reopen.

## Automod

### Rate-tracking race

**Problem:** `_increment_rate_tracking` (`src/core/automod/manager/tracking.py:1`) used SELECT-then-UPDATE/INSERT. Concurrent violations collided on the `UNIQUE` constraint, and the fail-open fast check let the violating message through.

**Fix:** The counter now uses a conditional `UPDATE`-first upsert with a re-applied `UPDATE` on lost-insert races.

## Avatars

### Unbounded checksum caches

**Problem:** `_avatar_checksum_cache` and `_icon_checksum_cache` in `src/api/routes/avatars.py:1` were plain dicts with no cap.

**Fix:** Both now go through `_cache_set_checksum`, which evicts the oldest entry after 4096 items.

### Stale checksum after re-upload

**Problem:** `upload_avatar` (`src/api/routes/users/avatar.py:1`) invalidated Valkey but not the route-local checksum dict, so clients kept receiving `304` for the old avatar.

**Fix:** The route now calls `_invalidate_user_avatar_cache` after a successful upload.

## Voice

### Unvalidated quality metrics

**Problem:** `update_quality_hint` (`src/core/voice/signaling/manager/quality_mixin.py:1`) stored arbitrary floats (negative, huge, `NaN`) verbatim, which poisoned the `BandwidthEstimator`.

**Fix:** Numeric metrics are now clamped to non-negative ranges; `NaN` and infinity are dropped.

## Messaging

### Concurrent DM creation

**Problem:** Two users creating the same DM concurrently both missed the lookup. The loser's `create_dm_lookup` INSERT hit `UNIQUE(user1,user2)` and surfaced as 500 (`src/core/messaging/services/conversation.py:1`).

**Fix:** The service now re-queries on a unique collision and returns the winner's conversation (idempotent).

## Middleware

### Malformed Content-Length

**Problem:** `int(content_length)` on a non-numeric header in `src/api/app.py:1` raised `ValueError` and surfaced as 500.

**Fix:** Malformed lengths now return `400`; numeric over-cap lengths still return `413`.

## Verification

- New regression tests: `src/tests/features/test_events_artifacts_batch.py` (19 tests) covers the items above.
- Impacted suites: 1643 tests passed (events, WebSocket, artifacts, reports, automod, avatars, voice, DSAR, messaging, features).
- Full suite: 7261 passed, 37 skipped.
- Lint and type checks: Ruff, `ruff format`, and Pyright are clean.
