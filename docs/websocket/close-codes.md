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
| `4006` | `SESSION_NO_LONGER_VALID` | resume session can no longer be used |
| `4007` | `INVALID_SEQUENCE` | resume sequence invalid |
| `4008` | `RATE_LIMITED` | gateway-level rate limit exceeded |
| `4009` | `SESSION_TIMED_OUT` | session expired while disconnected |
| `4010` | `INVALID_SHARD` | invalid shard requested |
| `4011` | `SHARDING_REQUIRED` | sharding required for this workload |
| `4012` | `INVALID_API_VERSION` | unsupported API or gateway version |
| `4013` | `INVALID_INTENTS` | malformed or unsupported intents |
| `4014` | `DISALLOWED_INTENTS` | requested intents are not permitted |

## Client Guidance

- treat `4004`, `4006`, `4007`, and `4009` as signals to stop trying to resume blindly
- re-identify only when the close reason indicates that a fresh session is appropriate
- back off when rate-limited instead of reconnecting in a tight loop
- log both the close code and the most recent opcode/event context for debugging
