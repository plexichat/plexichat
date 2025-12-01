# WebSocket Gateway Module

WebSocket gateway for real-time event delivery.

## Features

- Standard opcodes and close codes
- Connection lifecycle management
- Session management with resume support
- Heartbeat monitoring
- Gateway intents for event filtering
- zlib-stream compression support
- Rate limiting per connection
- Event replay on resume

## Setup

```python
from src.api import websocket

# Initialize gateway
websocket.setup(
    auth_module=auth,
    events_module=events,
    presence_module=presence,
    servers_module=servers,
    heartbeat_interval_ms=45000,
    session_timeout_ms=60000,
)

# Add to FastAPI app
app.include_router(websocket.get_router())
```

## Gateway Opcodes

| Opcode | Name | Direction | Description |
|--------|------|-----------|-------------|
| 0 | DISPATCH | Server -> Client | Event dispatch |
| 1 | HEARTBEAT | Client -> Server | Keep alive |
| 2 | IDENTIFY | Client -> Server | Authentication |
| 3 | PRESENCE_UPDATE | Client -> Server | Update presence |
| 4 | VOICE_STATE_UPDATE | Client -> Server | Voice state |
| 6 | RESUME | Client -> Server | Resume session |
| 7 | RECONNECT | Server -> Client | Request reconnect |
| 9 | INVALID_SESSION | Server -> Client | Session invalid |
| 10 | HELLO | Server -> Client | Initial handshake |
| 11 | HEARTBEAT_ACK | Server -> Client | Heartbeat response |
| 12 | SERVER_STATUS | Server -> Client | Server status update (shutdown/restart) |
| 13 | VERSION_CHECK | Server -> Client | Version compatibility check |

## Connection Lifecycle

1. Client connects to `/gateway`
2. Server sends HELLO with heartbeat_interval
3. Client sends IDENTIFY with token and intents
4. Server validates token, sends READY event
5. Client sends HEARTBEAT at interval
6. Server sends HEARTBEAT_ACK
7. Server dispatches events to client

## Identify Payload

```json
{
    "op": 2,
    "d": {
        "token": "user_session_token",
        "intents": 513,
        "properties": {
            "os": "windows",
            "browser": "chrome",
            "device": "desktop"
        },
        "compress": false
    }
}
```

## Resume Payload

```json
{
    "op": 6,
    "d": {
        "token": "user_session_token",
        "session_id": "abc123",
        "seq": 42
    }
}
```

## Close Codes

| Code | Name | Resumable |
|------|------|-----------|
| 4000 | Unknown Error | Yes |
| 4001 | Unknown Opcode | Yes |
| 4002 | Decode Error | Yes |
| 4003 | Not Authenticated | Yes |
| 4004 | Authentication Failed | No |
| 4005 | Already Authenticated | Yes |
| 4007 | Invalid Sequence | Yes |
| 4008 | Rate Limited | Yes |
| 4009 | Session Timed Out | Yes |
| 4013 | Invalid Intents | No |
| 4014 | Disallowed Intents | No |
| 4015 | Version Outdated | No |
| 4016 | Server Maintenance | Yes |
| 4017 | Server Shutdown | Yes |

## Compression

Enable zlib-stream compression by setting `compress: true` in IDENTIFY:

```json
{
    "op": 2,
    "d": {
        "token": "...",
        "compress": true
    }
}
```

Compressed messages end with `\x00\x00\xff\xff`.

## Rate Limiting

- 120 events per 60 seconds per connection
- Exceeding limit results in close code 4008

## Configuration

```yaml
gateway:
  heartbeat_interval_ms: 45000
  session_timeout_ms: 60000
  max_connections_per_user: 5
  rate_limit_per_minute: 120
```

## Server Shutdown Handling

The gateway supports graceful shutdown with client notification:

### SERVER_STATUS Opcode (12)

Sent by server before shutdown/restart:

```json
{
    "op": 12,
    "d": {
        "state": "shutting_down",
        "message": "Server shutting down",
        "closing_in_seconds": 2.0
    }
}
```

Possible states:
- `shutting_down` - Server is shutting down permanently
- `restarting` - Server will restart shortly
- `maintenance` - Server entering maintenance mode

### Programmatic Shutdown

```python
from src.api import websocket

# Broadcast status to all clients
await websocket.broadcast_server_status({
    "state": "restarting",
    "message": "Server update in progress",
    "estimated_downtime_seconds": 30
})

# Close all connections gracefully
await websocket.close_all_connections(
    close_code=4017,  # SERVER_SHUTDOWN
    reason="Server shutting down",
    notify_first=True,
    grace_period_seconds=2.0
)
```

### Client Handling

Clients should:
1. Listen for opcode 12 (SERVER_STATUS)
2. Save any pending state
3. Prepare for reconnection
4. On close code 4016/4017, attempt reconnection with exponential backoff

## Testing

```bash
pytest src/tests/websocket/ -v
```
