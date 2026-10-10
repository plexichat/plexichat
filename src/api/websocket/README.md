# WebSocket gateway

The WebSocket gateway delivers real-time events to connected clients. It authenticates connections, manages heartbeats and sessions, and dispatches events filtered by gateway intents.

The gateway WebSocket endpoint is ` /gateway`. It is not prefixed with `/api/v1`. REST health probes are separate: `GET /api/v1/health` for full health and `GET /api/v1/ready` for readiness. A root `GET /health` proxies to `GET /api/v1/health`.

## Structure

| File | Description |
|------|-------------|
| `__init__.py` | Module exports and `setup` helpers |
| `gateway.py` | WebSocket endpoint at `/gateway` |
| `connection.py` | Per-connection state and send/receive |
| `dispatcher.py` | Event dispatch and broadcast |
| `handlers.py` | Compatibility re-export of `handlers.OpcodeHandler` |
| `handlers/` | Opcode-specific handlers |
| `session.py` | Session storage and resume |
| `opcodes.py` | `GatewayOpcode` definitions (including `50`, `60`–`67`) |
| `intents.py` | Intent validation and filtering |
| `compression.py` | Payload compression |
| `artifacts.py` | Artifact subscription registry and relay |
| `sum.py` | PlexiSum workbook subscription registry and relay |

## Key components

### Gateway (`gateway.py`)

Handles the WebSocket lifecycle at `/gateway`:

- Accepts the connection and sends `HELLO` with the heartbeat interval.
- Routes inbound frames to `OpcodeHandler`.
- Monitors heartbeats and closes timed-out sessions with `SESSION_TIMED_OUT` (4009).
- Sweeps stale voice signaling connections.
- Cleans up artifact and PlexiSum subscriptions per connection on disconnect.

### Connection (`connection.py`)

Manages one client connection:

- Tracks authentication state and heartbeat.
- Provides `send_json` with per-connection rate limiting.
- Handles disconnect and cleanup.

### Dispatcher (`dispatcher.py`)

Fans out events:

- Sends to explicit user lists or server member sets.
- Filters by intents.
- Applies per-connection rate limiting (120 events per 60 seconds; exceeding closes with 4008).
- Supports cross-worker delivery via Valkey pub/sub.

### Session (`session.py`)

Stores session state for resume:

- Generates connection IDs and stores sessions.
- Supports `RESUME` with sequence replay.

## Usage

### Setup

```python
from src.api.websocket import setup, is_setup

setup(auth_module, presence_module, servers_module, events_module)

if is_setup():
    # Gateway is ready to accept connections at /gateway
    pass
```

`setup` registers the gateway router without an API prefix, so the endpoint resolves to `/gateway`.

### Dispatch events

```python
from src.api.websocket import get_dispatcher
from src.core.events.models import Event
from src.core.events.types import EventType

dispatcher = get_dispatcher()

event = Event(
    event_type=EventType.MESSAGE_CREATE,
    data={"id": "123", "content": "Hello"},
    server_id=server_id,
    channel_id=channel_id,
)

# Send to specific users
await dispatcher.dispatch_event(event, [user_id1, user_id2])

# Send to all members of a server
await dispatcher.dispatch_to_server(event, server_id)
```

## Connection flow

1. Client opens a WebSocket to `/gateway`.
2. Server sends `HELLO` (opcode `10`) with `heartbeat_interval`.
3. Client sends `IDENTIFY` (opcode `2`) with token and intents.
4. Server validates and sends `READY` (`DISPATCH` with `READY`).
5. Client starts the heartbeat loop (`HEARTBEAT` / `HEARTBEAT_ACK`).
6. Server dispatches events; client may `RESUME` (opcode `6`) after disconnect.

## Intents

Clients declare which events they want with intents. Validate intents with `validate_intents` before sending `IDENTIFY`.

```python
from src.core.events.types import GatewayIntent

intents = GatewayIntent.GUILDS | GatewayIntent.GUILD_MESSAGES
```

| Intent | Bit | Description |
|--------|-----|-------------|
| `GUILDS` | `1 << 0` | Guild create, update, delete, channels, roles |
| `GUILD_MEMBERS` | `1 << 1` | Guild member events (privileged) |
| `GUILD_BANS` | `1 << 2` | Guild ban events |
| `GUILD_EMOJIS` | `1 << 3` | Guild emoji events |
| `GUILD_INTEGRATIONS` | `1 << 4` | Guild integration events |
| `GUILD_WEBHOOKS` | `1 << 5` | Guild webhook events |
| `GUILD_INVITES` | `1 << 6` | Guild invite events |
| `GUILD_VOICE_STATES` | `1 << 7` | Voice state events |
| `GUILD_PRESENCES` | `1 << 8` | Presence events (privileged) |
| `GUILD_MESSAGES` | `1 << 9` | Guild message events |
| `GUILD_MESSAGE_REACTIONS` | `1 << 10` | Guild reaction events |
| `GUILD_MESSAGE_TYPING` | `1 << 11` | Guild typing events |
| `DIRECT_MESSAGES` | `1 << 12` | Direct message events |
| `DIRECT_MESSAGE_REACTIONS` | `1 << 13` | DM reaction events |
| `DIRECT_MESSAGE_TYPING` | `1 << 14` | DM typing events |
| `MESSAGE_CONTENT` | `1 << 15` | Message content (privileged) |
| `SCHEDULED_EVENTS` | `1 << 16` | Scheduled event events |
| `THREADS` | `1 << 17` | Thread events |
| `AUTOMOD` | `1 << 18` | Auto-moderation events |
| `AUDIT_LOG` | `1 << 19` | Audit log events |
| `ARTIFACTS` | `1 << 20` | Artifact collaboration events |
| `SUMS` | `1 << 21` | PlexiSum workbook collaboration events |

`ARTIFACTS` (`1 << 20`) gates `ARTIFACT_CREATE`, `ARTIFACT_UPDATE`, `ARTIFACT_DELETE`, and `ARTIFACT_OP`. `SUMS` (`1 << 21`) gates `WORKBOOK_UPDATE`, `SUM_OPERATION`, and `SUM_PRESENCE`.

Both `ARTIFACTS` and `SUMS` are included in `GatewayIntent.default_intents()` and `GatewayIntent.all_intents()`. Privileged intents are `GUILD_MEMBERS`, `GUILD_PRESENCES`, and `MESSAGE_CONTENT`.

## Opcodes

Standard opcodes live in `src/api/websocket/opcodes.py` as `GatewayOpcode`.

| Opcode | Name | Direction | Purpose |
|--------|------|-----------|---------|
| `0` | `DISPATCH` | S→C | Dispatch an event |
| `1` | `HEARTBEAT` | C→S | Heartbeat |
| `2` | `IDENTIFY` | C→S | Authenticate with token and intents |
| `3` | `PRESENCE_UPDATE` | C→S | Update presence |
| `4` | `VOICE_STATE_UPDATE` | C→S | Update voice state |
| `6` | `RESUME` | C→S | Resume a session |
| `7` | `RECONNECT` | S→C | Request reconnect |
| `8` | `REQUEST_GUILD_MEMBERS` | C→S | Request guild member list |
| `9` | `INVALID_SESSION` | S→C | Invalid session |
| `10` | `HELLO` | S→C | Initial hello with heartbeat interval |
| `11` | `HEARTBEAT_ACK` | S→C | Heartbeat acknowledgement |
| `12` | `SERVER_STATUS` | — | Server status (soft-rejected if sent by client) |
| `13` | `VERSION_CHECK` | — | Version check (soft-rejected if sent by client) |
| `20` | `VOICE_CONNECT` | C→S | Connect to voice |
| `21` | `VOICE_DISCONNECT` | C→S | Disconnect from voice |
| `22` | `VOICE_SDP_OFFER` | C→S | WebRTC SDP offer |
| `23` | `VOICE_SDP_ANSWER` | C→S | WebRTC SDP answer |
| `24` | `VOICE_ICE_CANDIDATE` | C→S | WebRTC ICE candidate |
| `25` | `VOICE_SPEAKING` | C→S | Speaking indicator |
| `26` | `VOICE_QUALITY` | C→S | Voice quality update |
| `30` | `INTERACTION_CREATE` | — | Application interaction (soft-rejected if sent by client) |
| `31` | `INTERACTION_RESPONSE` | — | Interaction response (soft-rejected if sent by client) |
| `40` | `TYPING_START` | C→S | Start typing |
| `41` | `TYPING_STOP` | C→S | Stop typing |
| `50` | `RATCHET_UPDATE` | — | Channel ratchet update (soft-rejected if sent by client) |
| `60` | `ARTIFACT_SUBSCRIBE` | C→S | Subscribe to an artifact |
| `61` | `ARTIFACT_UNSUBSCRIBE` | C→S | Unsubscribe from an artifact |
| `62` | `ARTIFACT_OP` | C→S→C | Relay a realtime artifact delta |
| `63` | `ARTIFACT_SYNC` | S→C | Artifact snapshot for late joiners |
| `64` | `SUM_SUBSCRIBE` | C→S | Subscribe to a PlexiSum workbook |
| `65` | `SUM_UNSUBSCRIBE` | C→S | Unsubscribe from a workbook |
| `66` | `SUM_OP` | C→S→C | Relay a workbook operation |
| `67` | `SUM_SYNC` | S→C | Workbook snapshot with revision and replay |

Artifact helpers live in `src/api/websocket/artifacts.py` (`relay_artifact_op`, `send_artifact_ack`, `send_artifact_sync`). PlexiSum helpers live in `src/api/websocket/sum.py` (`relay_sum_op`, `send_sum_sync`). Persisted `ARTIFACT_OP` frames carry a per-artifact `seq` in `d.seq` and are echoed to the actor for optimistic-concurrency acknowledgement.

## Rate limiting

- Outbound dispatch: 120 events per 60 seconds per connection. Exceeding closes with 4008 (`RATE_LIMITED`).
- Inbound throttling for expensive opcodes: `PRESENCE_UPDATE` (5 per 10 s), `TYPING_START` (10 per 10 s), `REQUEST_GUILD_MEMBERS` (2 per 10 s), `HEARTBEAT` (10 per 10 s). Over-budget frames are dropped without closing.

## Health and readiness

The gateway does not expose its own HTTP health endpoint. Use the REST probes:

- `GET /api/v1/health` — Full health with version and event metrics.
- `GET /api/v1/ready` — Minimal readiness probe for orchestrators (returns `{"status":"ready"}`).
- `GET /health` — Root proxy to `GET /api/v1/health`.

## Related

- `src/api/websocket/handlers/README.md` — Per-opcode handlers.
- `src/api/websocket/opcodes.py` — Authoritative opcode and close-code definitions.
- `src/core/events/types.py` — `GatewayIntent` and `EventType` definitions.
