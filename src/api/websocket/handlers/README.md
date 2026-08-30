# WebSocket handlers

Handlers process inbound WebSocket opcodes and dispatch events. Each file in this directory owns one domain of real-time behavior.

## Handlers

| File | Opcodes handled | Description |
|------|-----------------|-------------|
| `connection.py` | `HEARTBEAT` (`1`), `IDENTIFY` (`2`), `RESUME` (`6`) | Connection lifecycle, authentication, session resume, heartbeat |
| `guild.py` | `REQUEST_GUILD_MEMBERS` (`8`) | Guild member list queries |
| `presence.py` | `PRESENCE_UPDATE` (`3`), `TYPING_START` (`40`), `TYPING_STOP` (`41`) | Presence, custom status, activity, typing indicators |
| `voice.py` | `VOICE_STATE_UPDATE` (`4`), `VOICE_CONNECT` (`20`), `VOICE_DISCONNECT` (`21`), `VOICE_SDP_OFFER` (`22`), `VOICE_SDP_ANSWER` (`23`), `VOICE_ICE_CANDIDATE` (`24`), `VOICE_SPEAKING` (`25`), `VOICE_QUALITY` (`26`) | Voice state and WebRTC signaling |
| `artifacts.py` | `ARTIFACT_SUBSCRIBE` (`60`), `ARTIFACT_UNSUBSCRIBE` (`61`), `ARTIFACT_OP` (`62`) | Artifact real-time fabric: subscribe, unsubscribe, relay persisted ops |
| `sum.py` | `SUM_SUBSCRIBE` (`64`), `SUM_UNSUBSCRIBE` (`65`), `SUM_OP` (`66`) | PlexiSum real-time fabric: workbook subscribe, unsubscribe, apply and relay ops |
| `__init__.py` | — | Aggregator `OpcodeHandler` that delegates to the files above |

### `connection.py`

Manages the connection lifecycle:

- Validates `IDENTIFY` (token, intents) and creates the session.
- Validates `RESUME` (session ID, sequence) and replays missed events.
- Handles `HEARTBEAT` and updates liveness.
- Applies inbound rate limits and intent validation.

### `guild.py`

Handles guild-scoped queries:

- Fetches guild members with channel visibility checks.
- Applies per-connection throttling for `REQUEST_GUILD_MEMBERS`.

### `presence.py`

Tracks presence and typing:

- Validates and broadcasts `PRESENCE_UPDATE` to friends and shared server members.
- Starts and stops typing indicators per channel, filtering recipients by channel visibility.
- Clears typing state on disconnect.

### `voice.py`

Handles voice channel signaling:

- Processes `VOICE_STATE_UPDATE` and `VOICE_CONNECT` / `VOICE_DISCONNECT`.
- Relays `VOICE_SDP_OFFER`, `VOICE_SDP_ANSWER`, `VOICE_ICE_CANDIDATE`.
- Broadcasts `VOICE_SPEAKING` and `VOICE_QUALITY`.

### `artifacts.py`

Implements the artifact fabric for opcodes `60`–`63`:

- `ARTIFACT_SUBSCRIBE` validates `artifact_id`, checks `artifact.view` permission or conversation membership, registers the connection in `ArtifactSubscriptionRegistry`, and sends `ARTIFACT_SYNC` (`63`) with snapshot and replay of ops after `base_seq`.
- `ARTIFACT_UNSUBSCRIBE` removes the connection's subscription.
- `ARTIFACT_OP` validates `op_type` and payload size, checks `artifact.edit`, persists to the `artifact_ops` log, relays to other subscribers via `relay_artifact_op` (carrying `seq`), and acknowledges the actor with `send_artifact_ack`.

`ARTIFACT_SYNC` (`63`) is server-push only. Clients that echo it receive a soft-reject (ignored, no close).

### `sum.py`

Implements the PlexiSum fabric for opcodes `64`–`67`:

- `SUM_SUBSCRIBE` validates `workbook_id`, checks Sum authorization and `plexisum` capability, registers the connection in `SumSubscriptionRegistry`, and sends `SUM_SYNC` (`67`) with `revision` and replay after `base_revision`.
- `SUM_UNSUBSCRIBE` removes the workbook subscription for that connection only.
- `SUM_OP` validates the envelope (`operation_id`, `operation_type`, `sheet_id`, `base_revision`, `cells`), calls `SumManager.apply_operation`, relays to other subscribers, and on `SumConflictError` sends `SUM_SYNC` so the sender can rebase.

`SUM_SYNC` (`67`) is server-push only and is soft-rejected if echoed by a client.

## Opcodes and intents

- Channel ratchet updates use opcode `50` (`RATCHET_UPDATE`). This opcode is server-push only; inbound frames are soft-rejected.
- Artifacts use `60`–`63`. PlexiSum uses `64`–`67`. See `src/api/websocket/opcodes.py` for the authoritative list.
- Artifacts require intent `ARTIFACTS` (`1 << 20`). PlexiSum requires intent `SUMS` (`1 << 21`). Both intents are included in `GatewayIntent.default_intents()` and `GatewayIntent.all_intents()` (see `src/core/events/types.py` and `src/api/websocket/intents.py`).
- Server-push-only opcodes (`DISPATCH`, `RECONNECT`, `INVALID_SESSION`, `HELLO`, `HEARTBEAT_ACK`, `ARTIFACT_SYNC`, `SUM_SYNC`) and unimplemented extensions (`SERVER_STATUS`, `VERSION_CHECK`, `INTERACTION_CREATE`, `INTERACTION_RESPONSE`, `RATCHET_UPDATE`) are soft-rejected when sent by a client: the frame is ignored and logged, the connection stays open.

## Event dispatch

The dispatcher filters by intents and channel visibility:

- DM conversations: events go to both participants.
- Group conversations: events go to all participants.
- Server channels: events go to server members who can view the channel.
- Presence: events go to friends and members of shared servers.
- Artifact and Sum ops: events go to artifact or workbook subscribers who still pass authorization at relay time.

## Inbound rate limiting

`OpcodeHandler` throttles expensive opcodes before dispatch:

| Opcode | Limit |
|--------|-------|
| `PRESENCE_UPDATE` | 5 per 10 s |
| `TYPING_START` | 10 per 10 s |
| `REQUEST_GUILD_MEMBERS` | 2 per 10 s |
| `HEARTBEAT` | 10 per 10 s |

Over-budget frames are dropped without closing the connection.

## Error handling

- Unknown opcode closes with `UNKNOWN_OPCODE` (4001).
- Invalid payloads for authenticated handlers return `DECODE_ERROR` (4002).
- Unauthenticated access to authenticated opcodes closes with `NOT_AUTHENTICATED` (4003).
- Resource-level denials (unknown artifact or workbook, missing permission) do not close the connection. The request is ignored and logged; the session stays alive to avoid auth reconnect loops.
- Rate-limited dispatch closes with `RATE_LIMITED` (4008) only for outbound overflow; inbound throttling drops the frame.

## Gateway endpoint

The WebSocket gateway listens at `/gateway` (no `/api/v1` prefix). REST probes are at `/api/v1/health` and `/api/v1/ready` (`/health` at the root proxies to `/api/v1/health`).

## Related

- `src/api/websocket/opcodes.py` — `GatewayOpcode` and `GatewayCloseCode`.
- `src/api/websocket/intents.py` — `validate_intents` and intent descriptions.
- `src/api/websocket/artifacts.py` and `src/api/websocket/sum.py` — Subscription registries and relay helpers.
