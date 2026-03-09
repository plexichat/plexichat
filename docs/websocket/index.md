# WebSocket Gateway

The Plexichat gateway delivers real-time events and accepts client-side signaling such as heartbeats, identify, presence updates, typing, and voice messages.

## Gateway URL

`{{WEBSOCKET_URL}}`

## Core Lifecycle

1. connect to the gateway
2. receive `HELLO`
3. send `IDENTIFY` with a session or bot token
4. heartbeat on the provided interval
5. consume `DISPATCH` events such as `READY`, `MESSAGE_CREATE`, and `PRESENCE_UPDATE`
6. resume or reconnect when the connection is interrupted

## Payload Shape

Gateway payloads use the familiar structure:

```json
{"op":0,"d":{},"s":1,"t":"EVENT_NAME"}
```

| Field | Meaning |
|------|---------|
| `op` | opcode |
| `d` | event or command data |
| `s` | sequence number on dispatch payloads |
| `t` | dispatch event type when `op` is `DISPATCH` |

## Code-Defined Opcode Families

- core opcodes such as `HELLO`, `IDENTIFY`, `HEARTBEAT`, `RESUME`, `INVALID_SESSION`
- Plexichat status opcodes such as `SERVER_STATUS` and `VERSION_CHECK`
- voice signaling opcodes such as `VOICE_CONNECT` and `VOICE_ICE_CANDIDATE`
- typing opcodes `TYPING_START` and `TYPING_STOP`

See [Connection](connection.md), [Events](events.md), [Intents](intents.md), [Opcodes](opcodes.md), and [Close Codes](close-codes.md) for details.
