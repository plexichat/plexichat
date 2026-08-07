# PlexiSum real-time fabric

PlexiSum collaborative workbooks are kept in sync through the WebSocket gateway
using four dedicated opcodes (64-67). The fabric mirrors the artifact realtime
design: a per-process subscription registry, per-connection relay fan-out, and
revision-based bootstrap so late joiners and reconnects converge without
resubmitting operations.

## Opcodes

| Opcode | Name               | Direction        | Payload                                    |
|--------|--------------------|------------------|--------------------------------------------|
| 64     | `SUM_SUBSCRIBE`    | client → gateway | `{ workbook_id, base_revision }`           |
| 65     | `SUM_UNSUBSCRIBE`  | client → gateway | `{ workbook_id }`                          |
| 66     | `SUM_OP`           | both             | `{ workbook_id, actor_id?, op }`           |
| 67     | `SUM_SYNC`         | gateway → client | `{ workbook_id, revision, ops }`           |

`SUM_OP` frames relayed to subscribers carry the operation envelope produced by
`SumManager.apply_operation` (`operation_id`, `base_revision`,
`result_revision`, `operation_type`, `cells`, `sheet_id`, …). Clients use
`operation_id` for idempotent application and `base_revision` for ordering.

## Server layout

- `src/api/websocket/opcodes.py` — opcode constants (64-67).
- `src/api/websocket/sum.py` — `SumSubscriptionRegistry` (multi-tab safe:
  a connection may subscribe to several workbooks, and a workbook may be
  subscribed from several connections of the same user), plus
  `relay_sum_op(dispatcher, workbook_id, op, actor_id, exclude_user_id)` and
  `send_sum_sync(connection, workbook_id, revision, ops)`.
- `src/api/websocket/handlers/sum.py` — `SumHandler` registered in the opcode
  dispatcher; handles subscribe / unsubscribe / op / sync.
- `src/api/websocket/gateway.py` — connection teardown unsubscribes the
  connection from both the artifact and Sum registries.

### Authorization model

Every relay revalidates on the server before fan-out:

1. The acting user must still hold the `plexisum` capability (license/config
   gate, fail-closed).
2. The user must be authorized for the workbook via `SumManager._authorize`
   (sum grants, range permissions, or workbook ownership).
3. The subscribe request must resolve the workbook; unknown or unauthorized
   workbooks are rejected with a close code rather than silently subscribed.

Client-supplied capability labels are never trusted.

### Operation application

`SUM_OP` envelopes from the sender are applied through
`SumManager.apply_operation` on a worker thread:

- payloads are bounded (≤ 256 KB);
- `operation_id` acts as an idempotency key — replaying an already-applied op
  returns the existing result instead of double-applying;
- on `SumConflictError` the sender alone receives a `SUM_SYNC` with the current
  revision plus the ops after the sender's `base_revision` so the client can
  rebase and resubmit.

### Bootstrap and resume

`SUM_SYNC` is the fabric's catch-up primitive:

- on subscribe, the gateway sends the current revision and (when
  `base_revision` is behind) the ops since that revision;
- on reconnect, the client resubscribes with the last acked revision; the
  server replays only missing ops, and idempotent application makes the result
  exactly-once.

## Client layout

- `src/js/suite/shared/sum-realtime.js` — `SumRealtime` hub owning the
  subscription lifecycle for one workbook (subscribe on open, inbound
  `SUM_OP`/`SUM_SYNC` fan-out via `handleGatewayOp`/`handleGatewaySync`,
  unsubscribe on teardown). Any renderer — the current bounded grid or a future
  Univer adapter — consumes this same module.
- `src/js/websocket.js` — opcode constants, `SUM_SUBSCRIBE`/`SUM_UNSUBSCRIBE`
  send helpers (`subscribeSumWorkbook`, `unsubscribeSumWorkbook`), and
  dispatch of inbound 66/67 frames to `window.SumRealtime`.
- `src/js/suite/apps/plexisum/workspace.js` — subscribes on mount
  (`startRealtimeSync`), applies remote cell deltas via `applyRemoteOp`,
  adopts authoritative state via `onSumSync`, and falls back to a 15 s
  revision-poll plus the coarse `WORKBOOK_UPDATE` live event for deployments
  without a gateway.

## Guarantees and limits

- Realtime is a delta channel only; the REST `/operations?after_revision=`
  replay and workbook file are the durable source of truth.
- Relayed `SUM_OP` frames carry the **sender's** raw `cells` payload (only
  `result_revision` is taken from the server result). Recipients treat the
  frame as a local-grid hint; authoritative values always come from the
  viewport/replay REST paths, and clients skip already-applied revisions so a
  stale frame can never clobber newer cells.
- Reconnect is safe: the server drops subscriptions on disconnect, so the
  client re-subscribes on `READY`/`RESUMED` with its last acked revision and
  the server replays only missing ops (idempotent by `operation_id`).
- Relay failures never break the gateway connection (fail-safe).
- Per-connection rate limits apply to `SUM_OP` as with other mutation opcodes.
- Cross-worker delivery is handled by the same pub/sub listener (Valkey) that
  backs the artifact fabric.

## Tests

`src/tests/suite/test_sum_realtime.py` covers registry semantics, relay
authorization revalidation, bootstrap sync, idempotent replay, and conflict
recovery.
