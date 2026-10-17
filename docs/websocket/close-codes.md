# Gateway close codes

This page lists the WebSocket close codes defined in `src/api/websocket/opcodes.py` (`GatewayCloseCode`) and explains how to handle each code. Use `is_resumable()` in the same module to check whether you can resume a session.

## Standard codes

These codes use the WebSocket standard range.

| Code | Name | Meaning | Resumable |
|------|------|---------|-----------|
| `1000` | `NORMAL_CLOSURE` | Normal closure. The connection closed cleanly. | No |
| `1001` | `GOING_AWAY` | The endpoint is going away. Reconnect with a new session. | No |

## Application codes

These codes use the `4000`–`4017` range. The human-readable message comes from `CLOSE_CODE_MESSAGES` in `src/api/websocket/opcodes.py`.

| Code | Name | Meaning | Resumable | Recommended action |
|------|------|---------|-----------|--------------------|
| `4000` | `UNKNOWN_ERROR` | Generic internal error. | Yes | Resume with `RESUME` (`op: 6`). If resume fails, send `IDENTIFY`. |
| `4001` | `UNKNOWN_OPCODE` | You sent an unrecognized opcode. | Yes | Fix the opcode and resume. |
| `4002` | `DECODE_ERROR` | Invalid payload encoding or shape. | Yes | Fix the payload format and resume. |
| `4003` | `NOT_AUTHENTICATED` | You sent a message that requires authentication before `IDENTIFY`. | Yes | Resume or send `IDENTIFY` (`op: 2`). |
| `4004` | `AUTHENTICATION_FAILED` | The token is invalid or rejected. | No | Do not resume. Get a new token and send a fresh `IDENTIFY`. |
| `4005` | `ALREADY_AUTHENTICATED` | You sent `IDENTIFY` on a session that is already authenticated. | Yes | Resume instead of re-identifying. |
| `4007` | `INVALID_SEQ` | The resume sequence number is invalid. | Yes | Resume with the correct `seq` from the last `DISPATCH` (`s` field). |
| `4008` | `RATE_LIMITED` | You exceeded the gateway rate limit. | Yes | Back off, then resume. Do not reconnect in a tight loop. |
| `4009` | `SESSION_TIMED_OUT` | The session expired while disconnected. | Yes | Resume if the session is still in the resume window; otherwise `INVALID_SESSION` instructs you to re-identify. |
| `4010` | `INVALID_SHARD` | You requested an invalid shard. | No | Fix the sharding configuration and send a fresh `IDENTIFY`. |
| `4011` | `SHARDING_REQUIRED` | The server requires sharding for this workload. | No | Implement sharding and reconnect. |
| `4012` | `INVALID_API_VERSION` | You requested an unsupported API or gateway version. | No | Update the client version. |
| `4013` | `INVALID_INTENTS` | The `intents` bitmask is malformed or exceeds `4194303`. See [Intents](intents.md). | No | Fix the `intents` value and reconnect. |
| `4014` | `DISALLOWED_INTENTS` | You requested privileged intents without approval. | No | Remove `GUILD_MEMBERS`, `GUILD_PRESENCES`, or `MESSAGE_CONTENT` or request approval. |
| `4015` | `VERSION_OUTDATED` | The client must update before reconnecting. | No | Update the client. |
| `4016` | `SERVER_MAINTENANCE` | The server is entering maintenance mode. | No | Reconnect after maintenance completes. |
| `4017` | `SERVER_SHUTDOWN` | The server is shutting down intentionally. | No | Reconnect when the server is available again. |

The resumable set is defined by `RESUMABLE_CLOSE_CODES` in `src/api/websocket/opcodes.py`:

```python
RESUMABLE_CLOSE_CODES = {
    4000, 4001, 4002, 4003, 4005, 4007, 4008, 4009,
}
```

Call `is_resumable(code)` to test a code at runtime and `get_close_message(code)` to get the message.

## How to handle a close

1.  Record the close code, reason, and the last opcode and sequence (`s`) you received.
2.  Check whether the code is resumable:
    *   If resumable, send `RESUME` (`op: 6`) with `token`, `session_id`, and `seq`. See [Connection](connection.md#resume-a-session).
    *   If not resumable, start a new session with `IDENTIFY` (`op: 2`).
3.  If the server replies with `INVALID_SESSION` (`op: 9`) and `d: false`, the session is not resumable. Send a fresh `IDENTIFY`.
4.  When rate-limited (`4008`), apply exponential backoff before reconnecting.

## Related pages

*   [Connection](connection.md) – Resume and invalid session flow
*   [Opcodes](opcodes.md) – Opcode values
*   [Intents](intents.md) – How intents affect `4013` and `4014`
