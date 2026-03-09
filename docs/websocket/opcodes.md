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
| `20` | `VOICE_CONNECT` |
| `21` | `VOICE_DISCONNECT` |
| `22` | `VOICE_SDP_OFFER` |
| `23` | `VOICE_SDP_ANSWER` |
| `24` | `VOICE_ICE_CANDIDATE` |
| `30` | `SERVER_STATUS` |
| `31` | `VERSION_CHECK` |
| `40` | `TYPING_START` |
| `41` | `TYPING_STOP` |

## Usage Notes

- clients primarily send `IDENTIFY`, `HEARTBEAT`, `RESUME`, and optional state-change opcodes
- servers primarily send `HELLO`, `HEARTBEAT_ACK`, `RECONNECT`, `INVALID_SESSION`, and `DISPATCH`
- voice and typing opcodes are feature-specific extensions beyond the basic lifecycle

