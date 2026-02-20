# WebSocket Connection

Detailed guide for establishing and maintaining a WebSocket connection.

## Connecting

Connect to the gateway endpoint:

```
wss://api.plexichat.com/gateway
```

## HELLO (Opcode 10)

After connecting, the server sends a HELLO payload:

```json
{
  "op": 10,
  "d": {
    "heartbeat_interval": 45000
  }
}
```

| Field | Type | Description |
|-------|------|-------------|
| heartbeat_interval | int | Heartbeat interval in milliseconds |

## IDENTIFY (Opcode 2)

Send IDENTIFY to authenticate:

```json
{
  "op": 2,
  "d": {
    "token": "your_session_token",
    "properties": {
      "os": "windows",
      "browser": "plexichat",
      "device": "plexichat"
    },
    "compress": false,
    "intents": 513
  }
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| token | string | Yes | Session or bot token |
| properties | object | No | Client properties |
| compress | bool | No | Enable compression |
| intents | int | No | Gateway intents bitmask |

## READY Event

After successful IDENTIFY, receive READY:

```json
{
  "op": 0,
  "t": "READY",
  "s": 1,
  "d": {
    "user": {
      "id": "123456789012345678",
      "username": "johndoe"
    },
    "session_id": "session_id_here",
    "resume_gateway_url": "wss://api.plexichat.com/gateway"
  }
}
```

## Heartbeating

Send HEARTBEAT at the interval specified in HELLO:

```json
{
  "op": 1,
  "d": 251
}
```

The `d` field contains the last sequence number received, or `null` if none.

Server responds with HEARTBEAT_ACK:

```json
{
  "op": 11
}
```

**Important:** If no HEARTBEAT_ACK is received before the next heartbeat is due, consider the connection dead and reconnect.

## Resuming

If disconnected with a resumable close code, resume the session:

```json
{
  "op": 6,
  "d": {
    "token": "your_session_token",
    "session_id": "session_id_from_ready",
    "seq": 251
  }
}
```

| Field | Type | Description |
|-------|------|-------------|
| token | string | Session token |
| session_id | string | Session ID from READY |
| seq | int | Last sequence number received |

On successful resume, receive RESUMED event and missed events are replayed.

## INVALID_SESSION (Opcode 9)

If session cannot be resumed:

```json
{
  "op": 9,
  "d": false
}
```

| Value | Action |
|-------|--------|
| true | Wait 1-5 seconds, then RESUME |
| false | Start new session with IDENTIFY |

## Reconnection Strategy

1. Check if close code is reconnectable
2. If resumable, attempt RESUME
3. If INVALID_SESSION with `d: false`, send new IDENTIFY
4. Use exponential backoff for retries
5. Maximum retry delay: 60 seconds

## Compression

PlexiChat supports zlib compression. Enable with `compress: true` in IDENTIFY.

Compressed payloads are sent as binary frames and must be decompressed before parsing.

## Server Status (Opcode 12)

Server may send status updates:

```json
{
  "op": 12,
  "d": {
    "state": "maintenance",
    "message": "Scheduled maintenance in 5 minutes",
    "estimated_downtime_seconds": 1800,
    "restart_at": "2024-01-01T12:00:00Z"
  }
}
```

## Version Check (Opcode 13)

Server may request version verification:

```json
{
  "op": 13,
  "d": {
    "server_version": "a.1.0-1",
    "min_supported_version": "a.1.0-1",
    "update_recommended": true,
    "message": "A newer version is available"
  }
}
```

## Typing Indicators (Opcodes 40-41)

Typing indicators use lightweight WebSocket opcodes for real-time signaling.

### Timeout Hierarchy

To prevent flickering and ensure smooth UX, typing uses a coordinated timeout hierarchy:

| Component | Timeout | Purpose |
|-----------|---------|---------|
| Client Throttle | 3000ms | Minimum time between TYPING_START sends |
| Server Expiry | 6000ms | Server-side indicator auto-expiration |
| UI Timeout | 7000ms | Client-side indicator removal |

### TYPING_START (Opcode 40)

Send when user starts typing in a channel:

```json
{
  "op": 40,
  "d": {
    "channel_id": "123456789012345678"
  }
}
```

The server will:
1. Record the typing indicator with 6-second expiration
2. Broadcast TYPING_START event to channel members

### TYPING_STOP (Opcode 41)

Send when user stops typing (clears input, sends message, or switches channels):

```json
{
  "op": 41,
  "d": {
    "channel_id": "123456789012345678"
  }
}
```

The server will:
1. Remove the typing indicator from the database
2. Broadcast TYPING_STOP event to channel members

### Automatic Cleanup

When a user disconnects from the gateway, the server automatically:
1. Clears all typing indicators for that user
2. Broadcasts TYPING_STOP events to affected channels
3. Dispatches PRESENCE_UPDATE with offline status
