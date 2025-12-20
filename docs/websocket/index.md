# WebSocket Gateway

The PlexiChat WebSocket Gateway provides real-time event delivery for connected clients.

## Connection URL

```
ws://localhost:8000/gateway
wss://gateway.example.com/gateway  (production)
```

## Connection Flow

1. Connect to the WebSocket endpoint
2. Receive `HELLO` (opcode 10) with heartbeat interval
3. Send `IDENTIFY` (opcode 2) with authentication token
4. Receive `DISPATCH` with `READY` event
5. Begin sending `HEARTBEAT` (opcode 1) at the specified interval
6. Receive `HEARTBEAT_ACK` (opcode 11) for each heartbeat

## Payload Structure

All messages use JSON format:

```json
{
  "op": 0,
  "d": {},
  "s": 1,
  "t": "EVENT_NAME"
}
```

| Field | Type | Description |
|-------|------|-------------|
| op | int | Opcode |
| d | object | Event data payload |
| s | int? | Sequence number (DISPATCH only) |
| t | string? | Event name (DISPATCH only) |

## Opcodes

### Core Opcodes

| Code | Name | Direction | Description |
|------|------|-----------|-------------|
| 0 | DISPATCH | Server -> Client | Event dispatch |
| 1 | HEARTBEAT | Bidirectional | Keep connection alive |
| 2 | IDENTIFY | Client -> Server | Authenticate |
| 3 | PRESENCE_UPDATE | Client -> Server | Update presence |
| 4 | VOICE_STATE_UPDATE | Client -> Server | Update voice state |
| 6 | RESUME | Client -> Server | Resume session |
| 7 | RECONNECT | Server -> Client | Reconnect requested |
| 8 | REQUEST_GUILD_MEMBERS | Client -> Server | Request member list |
| 9 | INVALID_SESSION | Server -> Client | Session invalid |
| 10 | HELLO | Server -> Client | Initial handshake |
| 11 | HEARTBEAT_ACK | Server -> Client | Heartbeat acknowledged |
| 12 | SERVER_STATUS | Server -> Client | Server status update |
| 13 | VERSION_CHECK | Bidirectional | Version check |

### Voice Opcodes

| Code | Name | Direction | Description |
|------|------|-----------|-------------|
| 20 | VOICE_CONNECT | Client -> Server | Voice connection request |
| 21 | VOICE_DISCONNECT | Client -> Server | Voice disconnection |
| 22 | VOICE_SDP_OFFER | Bidirectional | WebRTC SDP offer |
| 23 | VOICE_SDP_ANSWER | Bidirectional | WebRTC SDP answer |
| 24 | VOICE_ICE_CANDIDATE | Bidirectional | WebRTC ICE candidate |
| 25 | VOICE_SPEAKING | Bidirectional | Speaking indicator |
| 26 | VOICE_QUALITY | Server -> Client | Voice quality metrics |

### Interaction Opcodes

| Code | Name | Direction | Description |
|------|------|-----------|-------------|
| 30 | INTERACTION_CREATE | Server -> Client | Application interaction |
| 31 | INTERACTION_RESPONSE | Client -> Server | Interaction response |

## Close Codes

| Code | Name | Reconnectable | Description |
|------|------|---------------|-------------|
| 4000 | UNKNOWN_ERROR | Yes | Unknown error |
| 4001 | UNKNOWN_OPCODE | Yes | Unknown opcode |
| 4002 | DECODE_ERROR | Yes | Decode error |
| 4003 | NOT_AUTHENTICATED | Yes | Not authenticated |
| 4004 | AUTHENTICATION_FAILED | No | Auth failed |
| 4005 | ALREADY_AUTHENTICATED | Yes | Already authenticated |
| 4007 | INVALID_SEQ | Yes | Invalid sequence |
| 4008 | RATE_LIMITED | Yes | Rate limited |
| 4009 | SESSION_TIMED_OUT | Yes | Session timed out |
| 4010 | INVALID_SHARD | No | Invalid shard |
| 4011 | SHARDING_REQUIRED | No | Sharding required |
| 4012 | INVALID_API_VERSION | No | Invalid API version |
| 4013 | INVALID_INTENTS | No | Invalid intents |
| 4014 | DISALLOWED_INTENTS | No | Disallowed intents |
| 4015 | VERSION_OUTDATED | No | Client version outdated |
| 4016 | SERVER_MAINTENANCE | Yes* | Server maintenance |
| 4017 | SERVER_SHUTDOWN | Yes* | Server shutting down |

*Reconnectable after maintenance/restart completes.

## Rate Limits

| Scope | Events | Window |
|-------|--------|--------|
| Per Connection | 120 | 60s |

Exceeding limits results in close code 4008 (RATE_LIMITED).

## Detailed Documentation

- [Connection](connection.md) - Connection lifecycle and authentication
- [Events](events.md) - Event types and payloads
- [Close Codes](close-codes.md) - Close code handling and reconnection
