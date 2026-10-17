# Gateway connection flow

This page describes how to connect to the Plexichat gateway, authenticate, maintain heartbeats, and resume a session. The implementation lives in `src/api/websocket/gateway.py`, `src/api/websocket/handlers/connection.py`, and `src/api/websocket/opcodes.py`.

## Before you begin

*   You need a valid session or bot token.
*   You need the gateway URL: `{{WEBSOCKET_URL}}` (path `/gateway`).
*   Decide which [intents](intents.md) you need. The default intents bitmask is `3178237`; `0` disables all dispatch events.

## Connect and receive HELLO

1.  Open a WebSocket connection to `{{WEBSOCKET_URL}}`.
2.  Wait for `HELLO` (`op: 10`). The server sends it immediately after `websocket.accept()`:

    ```json
    {"op": 10, "d": {"heartbeat_interval": 45000}}
    ```

    `heartbeat_interval` is in milliseconds. The reference implementation uses `45000` (45 seconds). You must send a `HEARTBEAT` at that interval.

## Authenticate with IDENTIFY

Send `IDENTIFY` (`op: 2`) to authenticate a new session. You must identify before sending any other opcode except `HEARTBEAT`.

```json
{
  "op": 2,
  "d": {
    "token": "SESSION_OR_BOT_TOKEN",
    "properties": {"os": "linux", "browser": "custom", "device": "custom"},
    "compress": false,
    "intents": 3178237
  }
}
```

| Field | Required | Description |
|-------|----------|-------------|
| `token` | Yes | Session or bot token. The server verifies it through `AuthManager.verify_token`. |
| `properties` | No | Client hints such as `os`, `browser`, and `device`. Defaults to an empty object. |
| `compress` | No | If `true`, the server enables zlib compression for subsequent frames (`Connection.enable_compression`). Defaults to `false`. |
| `intents` | No | Bitmask of [GatewayIntent](intents.md) values. Defaults to `3178237` (all non-privileged intents except `SCHEDULED_EVENTS`, `THREADS`, `AUTOMOD`, and `AUDIT_LOG`). Values must be an integer `0`–`4194303`; otherwise the server closes with `4013 INVALID_INTENTS`. |

If `IDENTIFY` succeeds, the server replies with a `DISPATCH` (`op: 0`) event where `t` is `READY`:

```json
{"op": 0, "t": "READY", "s": 1, "d": {"v": 10, "user": {"id": "123"}, "guilds": [], "session_id": "abc", "resume_gateway_url": "wss://gateway.example.com"}}
```

Save `session_id` and the sequence number `s`. You need both to resume. `resume_gateway_url` contains the URL to use when resuming.

Error cases:

*   Missing or invalid token → close `4004 AUTHENTICATION_FAILED`.
*   Invalid `intents` type or range (including booleans, strings, or values above `4194303`) → close `4013 INVALID_INTENTS`.
*   Privileged intents without approval → close `4014 DISALLOWED_INTENTS` (if enforced at the gateway layer).
*   Sending `IDENTIFY` when already in `READY` state → close `4005 ALREADY_AUTHENTICATED`. Resume instead.
*   Exceeding the per-user connection cap → close `4008 RATE_LIMITED`.

## Maintain heartbeats

Send `HEARTBEAT` (`op: 1`) every `heartbeat_interval` milliseconds:

```json
{"op": 1, "d": 42}
```

*   Set `d` to the last sequence number you received, or `null` if you have not received any `DISPATCH` yet.
*   The server replies with `HEARTBEAT_ACK` (`op: 11`):

    ```json
    {"op": 11}
    ```

*   The server also tracks liveness in `Connection.is_alive`. If you miss two consecutive heartbeat windows, the server closes with `4009 SESSION_TIMED_OUT`.

If heartbeats stop being acknowledged, reconnect. Do not queue heartbeats faster than the negotiated interval.

## Resume a session

If the connection drops and the close code is resumable (see [Close codes](close-codes.md)), you can resume.

1.  Reconnect to the gateway and wait for `HELLO` (`op: 10`).
2.  Send `RESUME` (`op: 6`):

    ```json
    {
      "op": 6,
      "d": {
        "token": "SESSION_OR_BOT_TOKEN",
        "session_id": "PREVIOUS_SESSION_ID",
        "seq": 42
      }
    }
    ```

    | Field | Required | Description |
    |-------|----------|-------------|
    | `token` | Yes | The same token you used to identify. |
    | `session_id` | Yes | The `session_id` from the `READY` payload. |
    | `seq` | Yes | The last sequence number you received. Must be an integer `>= 0` and not a boolean. |

3.  If resume succeeds, the server sends `DISPATCH` with `t: RESUMED` and replays missed events:

    ```json
    {"op": 0, "t": "RESUMED", "s": 42, "d": {}}
    ```

4.  If resume fails, the server sends `INVALID_SESSION` (`op: 9`):

    ```json
    {"op": 9, "d": false}
    ```

    When `d` is `false`, the session is not resumable. Send a fresh `IDENTIFY` (`op: 2`) to start a new session. When `d` is `true`, you can try to resume again immediately.

The server validates `seq` type and range. An invalid `seq` results in `INVALID_SESSION` with `d: false`.

## Handle server-requested reconnects

The server can request a reconnect with `RECONNECT` (`op: 7`). When you receive it, reconnect and resume if the close code is resumable, or identify if the server also closes the connection.

## Related signaling

After you are in `READY` state, you can also send:

*   `PRESENCE_UPDATE` (`op: 3`) – Update your presence.
*   Voice opcodes `20`–`26` – Voice session signaling.
*   Typing opcodes `40` `TYPING_START` and `41` `TYPING_STOP`.
*   Fabric opcodes `60`–`63` for artifacts and `64`–`67` for PlexiSum workbooks. See [Opcodes](opcodes.md).

For standard and application close codes and whether each code is resumable, see [Close codes](close-codes.md).
