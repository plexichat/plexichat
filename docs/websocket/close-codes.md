# Gateway Close Codes

The backend defines these application close codes in `src/api/websocket/opcodes.py`.

## Defined Close Codes

| Code | Name | Meaning |
|------|------|---------|
| `4000` | `UNKNOWN_ERROR` | generic internal error |
| `4001` | `UNKNOWN_OPCODE` | unrecognized opcode received |
| `4002` | `DECODE_ERROR` | invalid payload encoding or shape |
| `4003` | `NOT_AUTHENTICATED` | client tried to act before identifying |
| `4004` | `AUTHENTICATION_FAILED` | token invalid or rejected |
| `4005` | `ALREADY_AUTHENTICATED` | duplicate identify on an active session |
| `4007` | `INVALID_SEQ` | resume sequence invalid |
| `4008` | `RATE_LIMITED` | gateway-level rate limit exceeded |
| `4009` | `SESSION_TIMED_OUT` | session expired while disconnected |
| `4010` | `INVALID_SHARD` | invalid shard requested |
| `4011` | `SHARDING_REQUIRED` | sharding required for this workload |
| `4012` | `INVALID_API_VERSION` | unsupported API or gateway version |
| `4013` | `INVALID_INTENTS` | malformed or unsupported intents |
| `4014` | `DISALLOWED_INTENTS` | requested intents are not permitted |
| `4015` | `VERSION_OUTDATED` | client must update before reconnecting |
| `4016` | `SERVER_MAINTENANCE` | server is entering or in maintenance mode |
| `4017` | `SERVER_SHUTDOWN` | server is shutting down intentionally |

## Client Guidance

- treat `4004`, `4007`, and `4009` as signals to stop trying to resume blindly
- re-identify only when the close reason indicates that a fresh session is appropriate
- back off when rate-limited instead of reconnecting in a tight loop
- log both the close code and the most recent opcode/event context for debugging
