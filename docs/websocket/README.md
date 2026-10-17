# WebSocket gateway

This directory documents the Plexichat gateway. The gateway delivers real-time dispatch events and accepts client signaling such as heartbeats, identification, presence updates, and fabric operations.

Source of truth for opcode and close code values is `src/api/websocket/opcodes.py`. Intent bit values are defined in `src/core/events/types.py` (`GatewayIntent`).

## Pages

| Page | Content |
|------|---------|
| [Connection](connection.md) | Connect, receive `HELLO`, send `IDENTIFY`, heartbeat, and resume after disconnect. |
| [Opcodes](opcodes.md) | Integer opcode values grouped by family: lifecycle, status, voice, interactions, typing, ratchet, and real-time fabric. |
| [Intents](intents.md) | Intent bitmask values, default and privileged sets, and how to calculate the bitmask you send in `IDENTIFY`. |
| [Events](events.md) | `DISPATCH` (`op: 0`) event types and payload guidance. |
| [Close codes](close-codes.md) | Standard (`1000`, `1001`) and application (`4000`–`4017`) close codes, whether each code is resumable, and what to do next. |

Use these pages for gateway behavior. For REST endpoints, use the generated OpenAPI schema under `/docs`.
