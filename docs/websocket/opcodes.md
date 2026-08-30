# Gateway opcodes

The backend defines gateway opcodes in `src/api/websocket/opcodes.py` (`GatewayOpcode`). Clients send opcodes to the server; the server sends opcodes to clients.

## Core lifecycle opcodes

These opcodes manage the connection lifecycle. See [Connection](connection.md) for the full flow.

| Value | Name | Direction | Purpose |
|-------|------|-----------|---------|
| `0` | `DISPATCH` | Server → client | Deliver a dispatch event (`t` and `s` fields). |
| `1` | `HEARTBEAT` | Client → server | Heartbeat with the last sequence number or `null`. |
| `2` | `IDENTIFY` | Client → server | Authenticate a new session with `token`, `intents`, and `properties`. |
| `3` | `PRESENCE_UPDATE` | Client → server | Update presence. |
| `4` | `VOICE_STATE_UPDATE` | Client → server | Update voice state. |
| `6` | `RESUME` | Client → server | Resume a prior session with `token`, `session_id`, and `seq`. |
| `7` | `RECONNECT` | Server → client | Request that the client reconnect and resume. |
| `8` | `REQUEST_GUILD_MEMBERS` | Client → server | Request guild member chunks. |
| `9` | `INVALID_SESSION` | Server → client | Session is not resumable as sent (`d: false`) or can be retried (`d: true`). |
| `10` | `HELLO` | Server → client | Send heartbeat interval (`heartbeat_interval: 45000`). |
| `11` | `HEARTBEAT_ACK` | Server → client | Acknowledge a heartbeat. |

## Plexichat status opcodes

| Value | Name | Purpose |
|-------|------|---------|
| `12` | `SERVER_STATUS` | Server status notification. |
| `13` | `VERSION_CHECK` | Client version check. |

## Voice signaling opcodes

| Value | Name | Purpose |
|-------|------|---------|
| `20` | `VOICE_CONNECT` | Connect to voice. |
| `21` | `VOICE_DISCONNECT` | Disconnect from voice. |
| `22` | `VOICE_SDP_OFFER` | SDP offer. |
| `23` | `VOICE_SDP_ANSWER` | SDP answer. |
| `24` | `VOICE_ICE_CANDIDATE` | ICE candidate. |
| `25` | `VOICE_SPEAKING` | Speaking indicator. |
| `26` | `VOICE_QUALITY` | Voice quality stats. |

## Application interaction opcodes

| Value | Name | Purpose |
|-------|------|---------|
| `30` | `INTERACTION_CREATE` | Create an interaction. |
| `31` | `INTERACTION_RESPONSE` | Respond to an interaction. |

## Typing indicator opcodes

| Value | Name | Purpose |
|-------|------|---------|
| `40` | `TYPING_START` | Start typing. |
| `41` | `TYPING_STOP` | Stop typing. |

## Encryption ratchet opcode

| Value | Name | Purpose |
|-------|------|---------|
| `50` | `RATCHET_UPDATE` | Channel encryption ratchet key update. |

## Real-time fabric opcodes

Artifacts are collaborative canvases such as whiteboards and live code editors. Sums are collaborative PlexiSum workbooks. Both families use the same subscribe, operation, and sync pattern. For close handling, see [Close codes](close-codes.md).

| Value | Name | Purpose |
|-------|------|---------|
| `60` | `ARTIFACT_SUBSCRIBE` | Subscribe to an artifact's live updates. |
| `61` | `ARTIFACT_UNSUBSCRIBE` | Unsubscribe from an artifact. |
| `62` | `ARTIFACT_OP` | Apply an operation to an artifact. |
| `63` | `ARTIFACT_SYNC` | Request or receive full artifact state sync. |
| `64` | `SUM_SUBSCRIBE` | Subscribe to a PlexiSum workbook. |
| `65` | `SUM_UNSUBSCRIBE` | Unsubscribe from a workbook. |
| `66` | `SUM_OP` | Apply an operation to a workbook. |
| `67` | `SUM_SYNC` | Request or receive full workbook state sync. |

## How to use opcodes

*   As a client, you primarily send `IDENTIFY`, `HEARTBEAT`, `RESUME`, and optional state-change opcodes such as `PRESENCE_UPDATE`, `TYPING_START`, and fabric opcodes.
*   The server primarily sends `HELLO`, `HEARTBEAT_ACK`, `RECONNECT`, `INVALID_SESSION`, and `DISPATCH`.
*   Always check the opcode value against `GatewayOpcode` in `src/api/websocket/opcodes.py` before implementing custom handling. Unknown opcodes close the connection with `4001 UNKNOWN_OPCODE` (resumable).

