# PlexiChat API Reference

Complete API reference for PlexiChat server.

## Version Format

PlexiChat uses a stage-based versioning scheme:

```
[stage].[major].[minor]-[build]
```

| Component | Values | Description |
|-----------|--------|-------------|
| stage | `a`, `b`, `c`, `r` | Alpha, Beta, Candidate, Release |
| major | 1+ | Major version (breaking changes) |
| minor | 0+ | Minor version (new features) |
| build | 1+ | Build number (resets on minor bump) |

Examples: `a.1.0-1`, `b.2.3-15`, `r.1.0-1`

Current version: `a.1.0-1`

## Base URL

```
https://api.plexichat.com/api/v1
```

## Authentication

Include token in Authorization header:

```
Authorization: Bearer <session_token>
Authorization: Bot <bot_token>
```

## Error Format

All errors follow this format:

```json
{
  "error": {
    "code": 404,
    "message": "Resource not found"
  }
}
```

Version-related errors include additional fields:

```json
{
  "error": {
    "code": "VERSION_OUTDATED",
    "message": "Client version a.1.0-1 is no longer supported",
    "client_version": "a.1.0-1",
    "min_version": "a.1.2-1",
    "server_version": "a.1.5-10",
    "update_url": "https://..."
  }
}
```

---

## Version & Status Endpoints

These endpoints enable client-server version negotiation and server status monitoring.

### GET /version

Get server version information.

**Response:**
```json
{
  "version": {
    "stage": "a",
    "major": 1,
    "minor": 0,
    "build": 1,
    "string": "a.1.0-1"
  },
  "min_supported_version": {
    "stage": "a",
    "major": 1,
    "minor": 0,
    "build": 1,
    "string": "a.1.0-1"
  },
  "api_version": "v1"
}
```

### POST /version/negotiate

Negotiate version compatibility with the server.

**Request:**
```json
{
  "client_version": "a.1.0-1",
  "supported_versions": ["a.1.0-1", "a.1.1-1"]
}
```

**Response (Compatible):**
```json
{
  "compatible": true,
  "server_version": {
    "stage": "a",
    "major": 1,
    "minor": 0,
    "build": 1,
    "string": "a.1.0-1"
  },
  "client_version": {
    "stage": "a",
    "major": 1,
    "minor": 0,
    "build": 1,
    "string": "a.1.0-1"
  },
  "min_supported_version": null,
  "update_required": false,
  "update_recommended": false,
  "message": "Client version is compatible.",
  "update_url": null
}
```

**Response (Update Required - HTTP 426):**
```json
{
  "error": {
    "code": "VERSION_OUTDATED",
    "message": "Client version a.0.9-1 is no longer supported. Please update to at least a.1.0-1.",
    "client_version": "a.0.9-1",
    "min_version": "a.1.0-1",
    "server_version": "a.1.0-1",
    "update_url": "https://..."
  }
}
```

### GET /status

Get current server operational status.

**Response:**
```json
{
  "state": "running",
  "version": {
    "stage": "a",
    "major": 1,
    "minor": 0,
    "build": 1,
    "string": "a.1.0-1"
  },
  "uptime_seconds": 86400,
  "maintenance_message": null,
  "estimated_downtime_seconds": null,
  "restart_at": null
}
```

**Server States:**
| State | Description |
|-------|-------------|
| `running` | Normal operation |
| `maintenance` | Scheduled maintenance in progress |
| `shutting_down` | Server is shutting down |
| `restarting` | Server is restarting |

**Polling Recommendations:**
- Normal operation: Poll every 60 seconds
- Non-running state: Poll every 5 seconds

---

## Health Endpoint

### GET /health

Check API health status.

**Response:**
```json
{
  "status": "healthy",
  "version": "a.1.0-1"
}
```

---

## Authentication Endpoints

### POST /auth/register

Register a new user account.

**Request:**
```json
{
  "username": "johndoe",
  "email": "john@example.com",
  "password": "SecurePass123!"
}
```

**Response:**
```json
{
  "user_id": "123456789012345678",
  "username": "johndoe",
  "email": "john@example.com"
}
```

### POST /auth/login

Login to an existing account.

**Request:**
```json
{
  "email": "john@example.com",
  "password": "SecurePass123!"
}
```

**Response:**
```json
{
  "token": "session_token_here",
  "user": {
    "id": "123456789012345678",
    "username": "johndoe"
  },
  "requires_2fa": false
}
```

### POST /auth/2fa

Complete two-factor authentication.

**Request:**
```json
{
  "token": "partial_session_token",
  "code": "123456"
}
```

### POST /auth/logout

Logout current session.

**Headers:** `Authorization: Bearer <token>`

---

## User Endpoints

### GET /users/@me

Get current authenticated user.

**Headers:** `Authorization: Bearer <token>`

**Response:**
```json
{
  "id": "123456789012345678",
  "username": "johndoe",
  "email": "john@example.com",
  "avatar": "avatar_hash",
  "created_at": "2024-01-01T00:00:00Z"
}
```

### PATCH /users/@me

Update current user.

**Request:**
```json
{
  "username": "newusername",
  "avatar": "base64_image_data"
}
```

### GET /users/{user_id}

Get user by ID.

---

## Server Endpoints

### GET /servers

Get user's servers.

### POST /servers

Create a new server.

**Request:**
```json
{
  "name": "My Server",
  "icon": "base64_image_data"
}
```

### GET /servers/{server_id}

Get server details.

### PATCH /servers/{server_id}

Update server.

### DELETE /servers/{server_id}

Delete server.

### GET /servers/{server_id}/channels

Get server channels.

---

## Channel Endpoints

### GET /channels/{channel_id}

Get channel details.

### PATCH /channels/{channel_id}

Update channel.

### DELETE /channels/{channel_id}

Delete channel.

---

## Message Endpoints

### GET /channels/{channel_id}/messages

Get messages in a channel.

**Query Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| limit | int | Max messages to return (1-100, default 50) |
| before | snowflake | Get messages before this ID |
| after | snowflake | Get messages after this ID |

### POST /channels/{channel_id}/messages

Send a message.

**Request:**
```json
{
  "content": "Hello, world!",
  "attachments": []
}
```

### GET /channels/{channel_id}/messages/{message_id}

Get a specific message.

### PATCH /channels/{channel_id}/messages/{message_id}

Edit a message.

### DELETE /channels/{channel_id}/messages/{message_id}

Delete a message.

---

## Relationship Endpoints

### GET /relationships/@me

Get current user's relationships.

### POST /relationships

Send a friend request.

**Request:**
```json
{
  "user_id": "123456789012345678"
}
```

### PUT /relationships/{relationship_id}/accept

Accept a friend request.

### DELETE /relationships/{relationship_id}

Remove a relationship.

### POST /relationships/block

Block a user.

**Request:**
```json
{
  "user_id": "123456789012345678"
}
```

---

## Presence Endpoints

### PUT /users/@me/presence

Update current user's presence.

**Request:**
```json
{
  "status": "online",
  "custom_status": "Working on PlexiChat"
}
```

**Status Values:** `online`, `idle`, `dnd`, `invisible`, `offline`

### GET /users/{user_id}/presence

Get a user's presence.

---

## Reaction Endpoints

### PUT /channels/{channel_id}/messages/{message_id}/reactions/{emoji}

Add a reaction to a message.

### DELETE /channels/{channel_id}/messages/{message_id}/reactions/{emoji}

Remove your reaction from a message.

### GET /channels/{channel_id}/messages/{message_id}/reactions

Get all reactions on a message.

### GET /channels/{channel_id}/messages/{message_id}/reactions/{emoji}

Get users who reacted with a specific emoji.

---

## Webhook Endpoints

### POST /webhooks

Create a webhook.

**Request:**
```json
{
  "channel_id": "123456789012345678",
  "name": "My Webhook",
  "avatar": "base64_image_data"
}
```

### GET /webhooks/{webhook_id}

Get webhook details.

### DELETE /webhooks/{webhook_id}

Delete a webhook.

### POST /webhooks/{webhook_id}/{token}

Execute a webhook (send a message).

**Request:**
```json
{
  "content": "Webhook message",
  "username": "Custom Name",
  "avatar_url": "https://..."
}
```

---

## WebSocket Gateway

Connect to the WebSocket gateway for real-time events.

### Connection URL

```
wss://gateway.plexichat.com/?v=1
```

### Opcodes

| Code | Name | Description | Direction |
|------|------|-------------|-----------|
| 0 | DISPATCH | Event dispatch | Server -> Client |
| 1 | HEARTBEAT | Keep connection alive | Bidirectional |
| 2 | IDENTIFY | Authenticate connection | Client -> Server |
| 3 | PRESENCE_UPDATE | Update presence | Client -> Server |
| 6 | RESUME | Resume session | Client -> Server |
| 7 | RECONNECT | Server requests reconnect | Server -> Client |
| 9 | INVALID_SESSION | Session invalidated | Server -> Client |
| 10 | HELLO | Initial handshake | Server -> Client |
| 11 | HEARTBEAT_ACK | Heartbeat acknowledged | Server -> Client |
| 12 | SERVER_STATUS | Server status update | Server -> Client |
| 13 | VERSION_CHECK | Version compatibility check | Bidirectional |

### Close Codes

| Code | Name | Description | Reconnectable |
|------|------|-------------|---------------|
| 4000 | UNKNOWN_ERROR | Unknown error | Yes |
| 4001 | UNKNOWN_OPCODE | Unknown opcode | Yes |
| 4002 | DECODE_ERROR | Decode error | Yes |
| 4003 | NOT_AUTHENTICATED | Not authenticated | Yes |
| 4004 | AUTHENTICATION_FAILED | Auth failed | No |
| 4005 | ALREADY_AUTHENTICATED | Already authenticated | Yes |
| 4007 | INVALID_SEQ | Invalid sequence | Yes |
| 4008 | RATE_LIMITED | Rate limited | Yes |
| 4009 | SESSION_TIMED_OUT | Session timed out | Yes |
| 4012 | INVALID_API_VERSION | Invalid API version | No |
| 4015 | VERSION_OUTDATED | Client version outdated | No |
| 4016 | SERVER_MAINTENANCE | Server maintenance | Yes (after maintenance) |
| 4017 | SERVER_SHUTDOWN | Server shutting down | Yes (after restart) |

### Server Status Event (Opcode 12)

Sent when server state changes.

```json
{
  "op": 12,
  "d": {
    "state": "maintenance",
    "message": "Scheduled maintenance starting in 5 minutes",
    "estimated_downtime_seconds": 1800,
    "restart_at": "2024-01-01T12:00:00Z"
  }
}
```

### Version Check Event (Opcode 13)

Server may send to verify client version.

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

---

## Client Implementation Guidelines

### Version Checking

1. On startup, call `GET /version` to get server version info
2. Call `POST /version/negotiate` with client version
3. If `update_required` is true, prompt user to update
4. If `update_recommended` is true, show non-blocking notification

### Status Polling

1. Poll `GET /status` every 60 seconds during normal operation
2. When state is not `running`, increase polling to every 5 seconds
3. Display maintenance messages to users
4. Handle graceful disconnection on `shutting_down` state

### WebSocket Reconnection

1. On close code 4015 (VERSION_OUTDATED): Do not reconnect, prompt update
2. On close code 4016 (SERVER_MAINTENANCE): Wait for maintenance to end
3. On close code 4017 (SERVER_SHUTDOWN): Wait for server restart
4. On other resumable codes: Attempt reconnection with exponential backoff

### Error Handling

Always check for version-related error codes:
- `VERSION_OUTDATED`: Client must update
- `INVALID_VERSION_FORMAT`: Client sent malformed version string

---

## Rate Limits

| Endpoint Category | Requests | Window |
|-------------------|----------|--------|
| Authentication | 5 | 60s |
| Messages | 5 | 5s |
| General API | 50 | 60s |
| WebSocket | 120 events | 60s |

Rate limit headers:
```
X-RateLimit-Limit: 50
X-RateLimit-Remaining: 49
X-RateLimit-Reset: 1704067200
```
