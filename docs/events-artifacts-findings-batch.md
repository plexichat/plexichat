# Findings batch: events resilience, artifacts, reports, automod, avatars, voice, messaging

This documents the batch of findings fixed in this wave. Each entry names the
finding id (from the consolidated audit), what was wrong, and how it was fixed.

## Events layer (Agent 11)

- **B2 / B3 — idle-TTL eviction invisibility + critical bookkeeping**
  The subscriber idle-TTL sweep only DEBUG-logged evictions and could strip a
  critical subscriber's `_last_used` entry while leaving it registered.
  `_maybe_prune_locked` now:
  - skips critical callbacks entirely (they keep their `_last_used` entry, so
    cap-eviction selection and merged-pool bookkeeping stay consistent);
  - counts evictions in a new `evicted_subscribers` counter exposed via
    `get_loss_metrics()`;
  - logs evictions at WARNING (previously invisible at default levels).

  Files: `src/core/events/manager.py`

- **B5 — cross-worker echo protection dropped pre-identity events**
  Events published before `set_worker_id` are tagged `sw=unknown`; the
  consumer dropped any event whose tag matched its own worker id, so on
  multi-worker deployments siblings silently discarded each other's
  pre-identity events. The consumer now only skips a message whose source
  tag is non-empty, not `"unknown"`, and equals this worker's id.

  Files: `src/api/websocket/__init__.py`

- **C1 — silent exception swallowing in the emission path**
  `gateway_emit._emit` logged dispatch failures at DEBUG, hiding serialization
  or routing bugs at default levels. Failures are now logged at WARNING while
  staying non-raising.

  Files: `src/core/events/gateway_emit.py`

- **C3 — replay-buffer overflow lost events silently**
  `Session.add_replay_event` evicted the oldest buffered event with no signal.
  The first overflow per session now logs a WARNING describing the gap.

  Files: `src/api/websocket/session.py`

- **C5 — `close()` bypassed the send lock**
  `Connection.close()` could interleave a final frame after the close
  handshake when racing a concurrent `send_json`. It now acquires
  `_send_lock` around the websocket close.

- **C6 — zlib compression context not reset on send failure**
  A partially-failed compressed send left the shared zlib context mid-stream,
  corrupting every subsequent binary frame. `send_json` now rebuilds the
  compressobj on failure so the next frame starts from a clean boundary.

  Files: `src/api/websocket/connection.py`

- **C7 — dispatcher `on_event` deprecated loop lookup + silent drop**
  The fallback used `asyncio.get_event_loop()` (deprecated on 3.12+) and
  silently dropped events when no loop was available. It now prefers the
  running loop, falls back to the captured per-connection loop, and logs a
  WARNING when an event genuinely cannot be dispatched.

  Files: `src/api/websocket/dispatcher.py`

- **D8 — voice speaking channel-id int/str comparison**
  `handle_voice_speaking` compared the authoritative int `voice_state.channel_id`
  against the client-supplied string, so speaking updates never dispatched.
  The channel id is now coerced (and validated) before the comparison.

  Files: `src/api/websocket/handlers/voice.py`

- **D9 — /health events-metrics wiring dead**
  `src/api/routes/health.py` read `events_module.event_manager`, but only the
  private `_manager` existed — the advisory fields were always None. The
  events module now exposes a PEP 562 `event_manager` alias so the health
  endpoint reports real saturation metrics.

  Files: `src/core/events/__init__.py`, `src/tests/api/test_health_events_metrics.py`

## Artifacts (Agent 4)

- **M26 — global asyncio.Queue misuse in the transcription worker**
  The fallback scheduling queue was an unbounded `asyncio.Queue` created
  outside a running loop and put to from arbitrary threads. It is now a
  bounded (512) stdlib `queue.Queue` (thread-safe, no loop binding); the
  async drainer polls it, and a full queue drops the job with a WARNING
  instead of growing memory without bound.

- **M27 — multi-segment recordings never transcribed**
  `_resolve_local_recording_path` rejected `call_<id>_track_<n>.*` files, so
  consent-interrupted segmented calls were skipped entirely. Track files are
  now accepted alongside the single-file `call_<id>.` form.

- **L28 — `max_audio_minutes` documented but never enforced**
  The worker now skips transcription (with a WARNING) when the call's recorded
  duration exceeds the configured cap.

  Files: `src/core/artifacts/transcription/worker.py`

- **L30 — diarizer discovery retried on every call**
  A failed `_find_diarization_module()` / import was re-attempted per call
  (e.g. pyannote probing with no HF token). Failures are now memoized via
  `_diarization_failed` so transcription runs with "unknown" speakers.

  Files: `src/core/artifacts/transcription/provider.py`

- **L4 — unvalidated attachment size type**
  `convert_upload_to_artifact` compared a possibly-string `size` against the
  byte cap, raising TypeError → 500. The size is now coerced safely
  (unparseable → 0).

  Files: `src/core/artifacts/manager.py`

- **L7 — unbounded `list_artifacts` default**
  An omitted `limit` returned the entire table; only the HTTP route clamped.
  The repository now applies a `_DEFAULT_LIST_LIMIT` (200) so internal
  callers cannot materialize unbounded result sets.

- **L8 — `update_voice_call` allowed rewriting immutable columns**
  `id`, `artifact_id`, `created_at`, `updated_at` are now always ignored by
  the updater, so a stray value cannot rewrite identity/creation history.

  Files: `src/core/artifacts/repository.py`

## Reports (Agent 23)

- **F-BUG-1 — tuple-row fallback returned the priority column as a timestamp**
  Short tuple rows fell back to index 11/8 (PRIORITY) for `reported_at`,
  producing garbage timestamps. Missing timestamps are now `0`.

  Files: `src/core/reports/__init__.py`

- **F-BUG-3 — reopening a report kept stale review metadata**
  Setting a report back to `open` left `resolved_at`/`resolved_by`/
  `reviewed_at`/`reviewed_by` from the previous admin. The status-update SQL
  now clears all lifecycle metadata on reopen.

  Files: `src/api/routes/features/reports.py`

## Automod (Agent 6)

- **E4 — rate-tracking read-modify-write race**
  `_increment_rate_tracking` did SELECT-then-UPDATE/INSERT; concurrent
  violations collided on the UNIQUE constraint, and the fail-open fast check
  let the violating message through. The counter now uses a conditional
  UPDATE-first upsert with a re-applied UPDATE on lost-insert races.

  Files: `src/core/automod/manager/tracking.py`

## Avatars (Agent 7)

- **M5 — unbounded in-process checksum caches**
  `_avatar_checksum_cache` / `_icon_checksum_cache` were plain dicts with no
  cap; enumerating user/server ids grew them without bound. Both now go
  through `_cache_set_checksum`, which evicts the oldest entry past 4096.

  Files: `src/api/routes/avatars.py`

- **H1 — avatar re-upload left the route-local checksum cache stale**
  `upload_avatar` invalidated Valkey but never the avatars route's in-process
  checksum dict, so clients kept receiving 304 for the old avatar. The route
  now calls `_invalidate_user_avatar_cache` after a successful upload.

  Files: `src/api/routes/users/avatar.py`

## Voice (Agent 34)

- **M8 — unvalidated client quality metrics**
  `update_quality_hint` stored arbitrary floats (negative, huge, NaN) from
  clients verbatim, poisoning the shared BandwidthEstimator. Numeric metrics
  are now clamped to sane non-negative ranges; NaN/infinity are dropped.

  Files: `src/core/voice/signaling/manager/quality_mixin.py`

## Messaging (Agent 8)

- **H4 — concurrent DM creation surfaced an unhandled IntegrityError → 500**
  Two users creating the same DM concurrently both missed the lookup; the
  loser's `create_dm_lookup` INSERT died on UNIQUE(user1,user2) and escaped
  as a 500. The service now re-queries on a unique collision and returns the
  winner's conversation (idempotent).

  Files: `src/core/messaging/services/conversation.py`

## Middleware (Agent 46)

- **L2 — malformed Content-Length crashed the body-size middleware**
  `int(content_length)` on a non-numeric header raised ValueError → 500.
  Malformed lengths now return 400; numeric over-cap lengths still return 413.

  Files: `src/api/app.py`

## Verification

- New regression tests: `src/tests/features/test_events_artifacts_batch.py`
  (19 tests) covering B2/B3/B5/C1/C3/C5/C6/D9, M26/M27/L4/L7/L8/L28/L30,
  F-BUG-1/F-BUG-3, E4, M5, M8, H4.
- Impacted suites: 1643 passed (events, websocket, artifacts, reports,
  automod, avatars, voice, dsar, messaging, features).
- Full suite: 7261 passed, 37 skipped.
- Ruff, ruff format, pyright: clean.
