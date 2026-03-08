# Gateway Connection Flow

This page follows the connection contract implemented in the backend gateway.

## Connect

Open a WebSocket connection to `{{WEBSOCKET_URL}}`.

## HELLO

The server begins with `HELLO` (`op: 10`) and provides a heartbeat interval.

```json
{"op":10,"d":{"heartbeat_interval":45000}}
```

## IDENTIFY

Clients authenticate with `IDENTIFY` (`op: 2`).

```json
{
  "op": 2,
  "d": {
    "token": "SESSION_OR_BOT_TOKEN",
    "properties": {"os": "unknown", "browser": "custom", "device": "custom"},
    "compress": false,
    "intents": 0
  }
}
```

### Identify Notes

- `token` is required
- `properties`, `compress`, and `intents` are optional client hints
- bots and user sessions use the same gateway entry point

## READY

A successful identify results in a `DISPATCH` payload with `t: "READY"`.

Typical fields include the authenticated user, a `session_id`, and a `resume_gateway_url`.

## Heartbeats

Send `HEARTBEAT` (`op: 1`) using the most recent sequence number you have seen, or `null` if you have not yet received one.

The server responds with `HEARTBEAT_ACK` (`op: 11`). If heartbeats stop being acknowledged, reconnect.

## Resume Flow

If the connection drops but the session is still resumable, send `RESUME` (`op: 6`) with:

- the same token
- the prior `session_id`
- the last sequence number seen

A successful resume produces a `RESUMED` dispatch.

## Invalid Session

`INVALID_SESSION` (`op: 9`) tells the client that resume is not currently valid. Clients should fall back to a fresh `IDENTIFY` when resume is rejected.

## Related Signaling

The same gateway also carries:

- `PRESENCE_UPDATE` client messages
- voice signaling opcodes under the `20`-series
- typing opcodes `40` and `41`
- server status and version-check notifications
