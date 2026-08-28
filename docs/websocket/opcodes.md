# Gateway Opcodes

The backend defines gateway opcodes in `src/api/websocket/opcodes.py`.

## Core Lifecycle Opcodes

| Value | Name | Purpose |
|------|------|---------|
| `0` | `DISPATCH` | server event delivery |
| `1` | `HEARTBEAT` | client heartbeat |
| `2` | `IDENTIFY` | authenticate a new session |
| `3` | `PRESENCE_UPDATE` | client presence change |
| `4` | `VOICE_STATE_UPDATE` | voice state signaling |
| `6` | `RESUME` | resume a prior session |
| `7` | `RECONNECT` | server requests reconnect |
| `8` | `REQUEST_GUILD_MEMBERS` | member chunk request |
| `9` | `INVALID_SESSION` | session is not resumable as sent |
| `10` | `HELLO` | heartbeat interval negotiation |
| `11` | `HEARTBEAT_ACK` | heartbeat acknowledged |

## Plexichat-Specific Status Opcodes

| Value | Name |
|------|------|
| `12` | `SERVER_STATUS` |
| `13` | `VERSION_CHECK` |
| `20` | `VOICE_CONNECT` |
| `21` | `VOICE_DISCONNECT` |
| `22` | `VOICE_SDP_OFFER` |
| `23` | `VOICE_SDP_ANSWER` |
| `24` | `VOICE_ICE_CANDIDATE` |
| `25` | `VOICE_SPEAKING` |
| `26` | `VOICE_QUALITY` |
| `30` | `INTERACTION_CREATE` |
| `31` | `INTERACTION_RESPONSE` |
| `40` | `TYPING_START` |
| `41` | `TYPING_STOP` |

## Encryption Ratchet Opcodes

| Value | Name | Purpose |
|------|------|---------|
| `50` | `RATCHET_UPDATE` | channel encryption ratchet key update |

## Real-Time Fabric Opcodes

Artifacts are collaborative canvases (whiteboards, live code editors).
Sums are collaborative PlexiSum workbooks. Both families use the same
subscribe/op/sync shape.

| Value | Name | Purpose |
|------|------|---------|
| `60` | `ARTIFACT_SUBSCRIBE` | subscribe to an artifact's live updates |
| `61` | `ARTIFACT_UNSUBSCRIBE` | unsubscribe from an artifact |
| `62` | `ARTIFACT_OP` | apply an operation to an artifact |
| `63` | `ARTIFACT_SYNC` | request/receive full artifact state sync |
| `64` | `SUM_SUBSCRIBE` | subscribe to a PlexiSum workbook |
| `65` | `SUM_UNSUBSCRIBE` | unsubscribe from a workbook |
| `66` | `SUM_OP` | apply an operation to a workbook |
| `67` | `SUM_SYNC` | request/receive full workbook state sync |

## Usage Notes

- clients primarily send `IDENTIFY`, `HEARTBEAT`, `RESUME`, and optional state-change opcodes
- servers primarily send `HELLO`, `HEARTBEAT_ACK`, `RECONNECT`, `INVALID_SESSION`, and `DISPATCH`
- server-status, interaction, voice, and typing opcodes are Plexichat-specific extensions beyond the basic lifecycle

