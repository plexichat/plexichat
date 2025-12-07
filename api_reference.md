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
https://api.example.com/api/v1
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

## Version & Status Endpoints

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

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| username | string | 3-32 chars | Username |
| email | string | Valid email | Email address |
| password | string | Min 8 chars | Password |

**Response (201):**
```json
{
  "status": "success",
  "token": "session_token_here",
  "user": {
    "id": "123456789012345678",
    "username": "johndoe",
    "email": "john@example.com",
    "avatar_url": null,
    "created_at": 1704067200000,
    "email_verified": true,
    "totp_enabled": false
  },
  "challenge_token": null,
  "methods": null,
  "expires_in": null
}
```

**Note:** `email_verified` is `true` by default when email verification is not required (default configuration). When email verification is enabled in server config, it will be `false` until verified.
```

**Error Responses:**
- `400` - Invalid input or weak password
- `409` - Username or email already exists

### POST /auth/login

Authenticate a user.

**Request:**
```json
{
  "username": "johndoe",
  "password": "SecurePass123!"
}
```

**Response (Success):**
```json
{
  "status": "success",
  "token": "session_token_here",
  "user": {
    "id": "123456789012345678",
    "username": "johndoe",
    "email": "john@example.com",
    "avatar_url": null,
    "created_at": 1704067200000,
    "email_verified": true,
    "totp_enabled": false
  },
  "challenge_token": null,
  "methods": null,
  "expires_in": null
}
```

**Response (2FA Required):**
```json
{
  "status": "two_factor_required",
  "token": null,
  "user": null,
  "challenge_token": "challenge_token_here",
  "methods": ["totp", "backup_code"],
  "expires_in": 300
}
```

**Error Responses:**
- `401` - Invalid credentials
- `403` - Account locked or email not verified

### POST /auth/2fa

Complete two-factor authentication.

**Request:**
```json
{
  "challenge_token": "challenge_token_from_login",
  "code": "123456"
}
```

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| challenge_token | string | Required | Challenge token from login |
| code | string | 6-8 chars | TOTP code or backup code |

**Response (200):**
```json
{
  "status": "success",
  "token": "session_token_here",
  "user": {
    "id": "123456789012345678",
    "username": "johndoe"
  },
  "challenge_token": null,
  "methods": null,
  "expires_in": null
}
```

**Error Responses:**
- `401` - Invalid or expired code/token

### POST /auth/logout

Logout current session. Requires authentication.

**Headers:** `Authorization: Bearer <token>`

**Response (200):**
```json
{
  "success": true
}
```

### GET /auth/2fa/status

Get current 2FA status for the user. Requires authentication.

**Headers:** `Authorization: Bearer <token>`

**Response (200):**
```json
{
  "enabled": false,
  "backup_codes_remaining": 0
}
```

### POST /auth/2fa/enable

Enable 2FA - returns secret and QR URI. Requires authentication.

**Headers:** `Authorization: Bearer <token>`

**Request:**
```json
{
  "password": "CurrentPassword123!"
}
```

**Response (200):**
```json
{
  "secret": "JBSWY3DPEHPK3PXP",
  "qr_uri": "otpauth://totp/PlexiChat:username?secret=JBSWY3DPEHPK3PXP&issuer=PlexiChat",
  "backup_codes": ["XXXX-XXXX", "YYYY-YYYY", "..."]
}
```

**Error Responses:**
- `400` - Password required
- `401` - Invalid password
- `409` - 2FA is already enabled

### POST /auth/2fa/confirm

Confirm 2FA setup with TOTP code. Requires authentication.

**Headers:** `Authorization: Bearer <token>`

**Request:**
```json
{
  "code": "123456"
}
```

**Response (200):**
```json
{
  "success": true
}
```

**Error Responses:**
- `400` - Invalid code format or 2FA setup not started
- `401` - Invalid code

### POST /auth/2fa/disable

Disable 2FA. Requires authentication.

**Headers:** `Authorization: Bearer <token>`

**Request:**
```json
{
  "password": "CurrentPassword123!",
  "code": "123456"
}
```

**Response (200):**
```json
{
  "success": true
}
```

**Error Responses:**
- `400` - Password or code required, or 2FA not enabled
- `401` - Invalid password or code

### GET /auth/sessions

Get all active sessions for the current user. Requires authentication.

**Headers:** `Authorization: Bearer <token>`

**Response (200):**
```json
[
  {
    "id": "123456789012345678",
    "user_id": "123456789012345678",
    "ip_address": "192.168.1.1",
    "user_agent": "Mozilla/5.0...",
    "created_at": 1704067200000,
    "last_used_at": 1704153600000,
    "current": true
  }
]
```

**Note:** The `current` field indicates if this is the session making the request.

### DELETE /auth/sessions/{session_id}

Revoke a specific session. Requires authentication.

**Headers:** `Authorization: Bearer <token>`

**Response (200):**
```json
{
  "success": true
}
```

**Error Responses:**
- `400` - Invalid session ID
- `404` - Session not found

---

## User Endpoints

### GET /users/@me

Get current authenticated user. Requires authentication.

**Headers:** `Authorization: Bearer <token>`

**Response (200):**
```json
{
  "id": "123456789012345678",
  "username": "johndoe",
  "email": "john@example.com",
  "avatar_url": "https://cdn.example.com/avatars/123.png",
  "created_at": 1704067200000,
  "email_verified": true,
  "totp_enabled": false
}
```

### PATCH /users/@me

Update current user profile. Requires authentication.

**Headers:** `Authorization: Bearer <token>`

**Request:**
```json
{
  "username": "newusername",
  "email": "newemail@example.com",
  "avatar_url": "https://cdn.example.com/avatars/new.png",
  "password": "NewSecurePass123!",
  "current_password": "OldPassword123!"
}
```

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| username | string | 3-32 chars, optional | New username |
| email | string | Valid email, optional | New email |
| avatar_url | string | Optional | New avatar URL |
| password | string | Min 8 chars, optional | New password |
| current_password | string | Required if changing password | Current password |

**Response (200):** Updated user object

**Error Responses:**
- `400` - Invalid input or weak password
- `409` - Username or email already exists

### GET /users/{user_id}

Get public user information. Requires authentication.

**Headers:** `Authorization: Bearer <token>`

**Response (200):**
```json
{
  "id": "123456789012345678",
  "username": "johndoe",
  "avatar_url": "https://cdn.example.com/avatars/123.png",
  "created_at": 1704067200000
}
```

**Error Responses:**
- `400` - Invalid user ID
- `404` - User not found

### GET /users/search

Search for a user by username. Requires authentication.

**Headers:** `Authorization: Bearer <token>`

**Query Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| username | string | Yes | Username to search for (exact match, case-insensitive) |

**Response (200):**
```json
{
  "id": "123456789012345678",
  "username": "johndoe",
  "avatar_url": "https://cdn.example.com/avatars/123.png",
  "created_at": 1704067200000
}
```

**Error Responses:**
- `400` - Username required
- `404` - User not found

### GET /users/@me/channels

Get all DM channels for the current user. Requires authentication.

**Headers:** `Authorization: Bearer <token>`

**Response (200):**
```json
[
  {
    "id": "123456789012345678",
    "type": "dm",
    "recipient_id": "234567890123456789",
    "recipient": {
      "id": "234567890123456789",
      "username": "janedoe"
    },
    "last_message_id": "345678901234567890"
  }
]
```

### POST /users/@me/channels

Create or get a DM channel with a user. Requires authentication.

**Headers:** `Authorization: Bearer <token>`

**Request:**
```json
{
  "recipient_id": "123456789012345678"
}
```

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| recipient_id | string | Snowflake ID, required | Target user ID |

**Response (200):**
```json
{
  "id": "123456789012345678",
  "type": "dm",
  "recipient_id": "234567890123456789",
  "recipient": {
    "id": "234567890123456789",
    "username": "janedoe"
  }
}
```

**Error Responses:**
- `400` - recipient_id required or invalid
- `403` - Cannot message this user (blocked)
- `404` - User not found

---

## Server Endpoints

### GET /servers

Get all servers the user is a member of. Requires authentication.

**Headers:** `Authorization: Bearer <token>`

**Response (200):**
```json
[
  {
    "id": "123456789012345678",
    "name": "My Server",
    "description": "A cool server",
    "icon_url": "https://cdn.example.com/icons/123.png",
    "owner_id": "123456789012345678",
    "member_count": 150,
    "default_channel_id": "234567890123456789",
    "created_at": 1704067200000
  }
]
```

### POST /servers

Create a new server. Requires authentication.

**Headers:** `Authorization: Bearer <token>`

**Request:**
```json
{
  "name": "My Server",
  "description": "A cool server",
  "icon_url": "https://cdn.example.com/icons/new.png"
}
```

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| name | string | 2-100 chars | Server name |
| description | string | Max 1000 chars, optional | Server description |
| icon_url | string | Optional | Server icon URL |

**Response (200):** Created server object

**Error Responses:**
- `400` - Server limit reached

### GET /servers/{server_id}

Get server details. Requires authentication and membership.

**Headers:** `Authorization: Bearer <token>`

**Response (200):** Server object

**Error Responses:**
- `400` - Invalid server ID
- `403` - Access denied
- `404` - Server not found

### PATCH /servers/{server_id}

Update server settings. Requires manage server permission.

**Headers:** `Authorization: Bearer <token>`

**Request:**
```json
{
  "name": "Updated Name",
  "description": "Updated description",
  "icon_url": "https://cdn.example.com/icons/updated.png",
  "default_channel_id": "123456789012345678"
}
```

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| name | string | 2-100 chars, optional | Server name |
| description | string | Max 1000 chars, optional | Server description |
| icon_url | string | Optional | Server icon URL |
| default_channel_id | string | Snowflake ID, optional | Default channel to select when joining server |

**Response (200):** Updated server object

**Error Responses:**
- `403` - Permission denied
- `404` - Server not found or default channel not found in server

### DELETE /servers/{server_id}

Delete a server. Only the owner can delete.

**Headers:** `Authorization: Bearer <token>`

**Response (200):**
```json
{
  "success": true
}
```

**Error Responses:**
- `403` - Not the owner
- `404` - Server not found

### GET /servers/{server_id}/channels

Get all channels in a server. Requires authentication and membership.

**Headers:** `Authorization: Bearer <token>`

**Response (200):**
```json
[
  {
    "id": "123456789012345678",
    "server_id": "123456789012345678",
    "name": "general",
    "channel_type": "text",
    "topic": "General discussion",
    "position": 0,
    "category_id": null,
    "nsfw": false,
    "slowmode_seconds": 0,
    "created_at": 1704067200000
  }
]
```

**Channel Types:** `text`, `voice`, `category`

**Error Responses:**
- `403` - Access denied
- `404` - Server not found

### GET /servers/{server_id}/members

Get members of a server. Requires authentication and membership.

**Headers:** `Authorization: Bearer <token>`

**Query Parameters:**
| Parameter | Type | Default | Constraints | Description |
|-----------|------|---------|-------------|-------------|
| limit | int | 100 | 1-1000 | Max members to return |
| after | string | null | Snowflake ID | Get members after this user ID |

**Response (200):**
```json
[
  {
    "user_id": "123456789012345678",
    "username": "johndoe",
    "avatar_url": "https://cdn.example.com/avatars/123.png",
    "nickname": "John",
    "roles": ["234567890123456789"],
    "joined_at": 1704067200000
  }
]
```

**Error Responses:**
- `403` - Access denied
- `404` - Server not found

### GET /servers/{server_id}/webhooks

Get all webhooks in a server. Requires authentication and manage webhooks permission.

**Headers:** `Authorization: Bearer <token>`

**Response (200):**
```json
[
  {
    "id": "123456789012345678",
    "channel_id": "234567890123456789",
    "server_id": "123456789012345678",
    "creator_id": "345678901234567890",
    "name": "My Webhook",
    "avatar_url": "https://cdn.example.com/avatars/webhook.png",
    "created_at": 1704067200000
  }
]
```

**Error Responses:**
- `403` - Permission denied
- `404` - Server not found

### POST /servers/{server_id}/icon

Upload a server icon. Requires manage server permission.

**Headers:** `Authorization: Bearer <token>`

**Request:** Multipart form data with `file` field (JPEG, PNG, GIF, or WebP, max 2MB)

**Response (200):** Updated server object with new `icon_url`

**Error Responses:**
- `400` - Invalid file type or file too large
- `403` - Permission denied
- `404` - Server not found

### GET /servers/{server_id}/roles

Get all roles in a server. Requires authentication and membership.

**Headers:** `Authorization: Bearer <token>`

**Response (200):**
```json
[
  {
    "id": "123456789012345678",
    "server_id": "123456789012345678",
    "name": "Admin",
    "color": "#FF0000",
    "position": 1,
    "permissions": {},
    "hoist": true,
    "mentionable": true,
    "is_default": false
  }
]
```

### POST /servers/{server_id}/roles

Create a new role. Requires manage roles permission.

**Headers:** `Authorization: Bearer <token>`

**Request:**
```json
{
  "name": "New Role",
  "color": "#00FF00",
  "permissions": {},
  "hoist": false,
  "mentionable": false
}
```

**Response (200):** Created role object

### PATCH /servers/{server_id}/roles/{role_id}

Update a role. Requires manage roles permission.

**Headers:** `Authorization: Bearer <token>`

**Response (200):** Updated role object

### DELETE /servers/{server_id}/roles/{role_id}

Delete a role. Requires manage roles permission.

**Headers:** `Authorization: Bearer <token>`

**Response (200):**
```json
{
  "success": true
}
```

### GET /servers/{server_id}/bans

Get all bans in a server. Requires ban members permission.

**Headers:** `Authorization: Bearer <token>`

**Response (200):**
```json
[
  {
    "user_id": "123456789012345678",
    "reason": "Spam",
    "banned_by": "234567890123456789",
    "banned_at": 1704067200000
  }
]
```

### PUT /servers/{server_id}/bans/{user_id}

Ban a user from a server. Requires ban members permission.

**Headers:** `Authorization: Bearer <token>`

**Request:**
```json
{
  "reason": "Spam",
  "delete_message_days": 1
}
```

**Response (200):**
```json
{
  "success": true
}
```

### DELETE /servers/{server_id}/bans/{user_id}

Unban a user from a server. Requires ban members permission.

**Headers:** `Authorization: Bearer <token>`

**Response (200):**
```json
{
  "success": true
}
```

### GET /servers/{server_id}/invites

Get all invites for a server. Requires manage server permission.

**Headers:** `Authorization: Bearer <token>`

**Response (200):**
```json
[
  {
    "code": "abc123",
    "server_id": "123456789012345678",
    "channel_id": "234567890123456789",
    "inviter_id": "345678901234567890",
    "uses": 5,
    "max_uses": 100,
    "max_age": 86400,
    "temporary": false,
    "created_at": 1704067200000,
    "expires_at": 1704153600000
  }
]
```

### GET /servers/{server_id}/audit-logs

Get audit log entries for a server. Requires view audit log permission.

**Headers:** `Authorization: Bearer <token>`

**Query Parameters:**
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| limit | int | 50 | Max entries to return |

**Response (200):**
```json
[
  {
    "id": "123456789012345678",
    "server_id": "123456789012345678",
    "user_id": "234567890123456789",
    "action": "channel_create",
    "target_type": "channel",
    "target_id": "345678901234567890",
    "changes": null,
    "reason": null,
    "created_at": 1704067200000
  }
]
```

**Audit Log Actions:**
- `server_create`, `server_update`, `server_delete`
- `channel_create`, `channel_update`, `channel_delete`
- `role_create`, `role_update`, `role_delete`
- `member_kick`, `member_ban`, `member_unban`
- `invite_create`, `invite_delete`
- `webhook_create`, `webhook_update`, `webhook_delete`
- `emoji_create`, `emoji_update`, `emoji_delete`
- `event_create`, `event_update`, `event_delete`

---

## Custom Emoji Endpoints

### GET /servers/{server_id}/emojis

Get all custom emojis in a server. Requires authentication and membership.

**Headers:** `Authorization: Bearer <token>`

**Response (200):**
```json
[
  {
    "id": "123456789012345678",
    "server_id": "123456789012345678",
    "name": "custom_emoji",
    "animated": false,
    "url": "/api/v1/media/emojis/123.png",
    "created_by": "234567890123456789",
    "created_at": 1704067200000
  }
]
```

### GET /servers/{server_id}/emojis/counts

Get emoji counts and limits for a server.

**Headers:** `Authorization: Bearer <token>`

**Response (200):**
```json
{
  "static_count": 10,
  "animated_count": 5,
  "static_limit": 50,
  "animated_limit": 50
}
```

### POST /servers/{server_id}/emojis

Create a custom emoji. Requires manage emojis permission.

**Headers:** `Authorization: Bearer <token>`

**Request:** Multipart form data with `name` and `image` fields

**Response (200):** Created emoji object

### PATCH /servers/{server_id}/emojis/{emoji_id}

Update emoji name. Requires manage emojis permission.

**Headers:** `Authorization: Bearer <token>`

**Request:**
```json
{
  "name": "new_name"
}
```

**Response (200):** Updated emoji object

### DELETE /servers/{server_id}/emojis/{emoji_id}

Delete a custom emoji. Requires manage emojis permission.

**Headers:** `Authorization: Bearer <token>`

**Response (200):**
```json
{
  "success": true
}
```

---

## Channel Endpoints

### GET /channels/{channel_id}

Get channel details. Requires authentication and access.

**Headers:** `Authorization: Bearer <token>`

**Response (200):**
```json
{
  "id": "123456789012345678",
  "server_id": "123456789012345678",
  "name": "general",
  "channel_type": "text",
  "topic": "General discussion",
  "position": 0,
  "category_id": null,
  "nsfw": false,
  "slowmode_seconds": 0,
  "created_at": 1704067200000
}
```

**Error Responses:**
- `400` - Invalid channel ID
- `403` - Access denied
- `404` - Channel not found

### PATCH /channels/{channel_id}

Update channel settings. Requires manage channels permission.

**Headers:** `Authorization: Bearer <token>`

**Request:**
```json
{
  "name": "updated-channel",
  "topic": "Updated topic",
  "position": 1,
  "nsfw": false,
  "slowmode_seconds": 5
}
```

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| name | string | 1-100 chars, optional | Channel name |
| topic | string | Max 1024 chars, optional | Channel topic |
| position | int | >= 0, optional | Channel position |
| nsfw | bool | Optional | NSFW flag |
| slowmode_seconds | int | 0-21600, optional | Slowmode delay |

**Response (200):** Updated channel object

**Error Responses:**
- `403` - Permission denied
- `404` - Channel not found

### DELETE /channels/{channel_id}

Delete a channel. Requires manage channels permission.

**Headers:** `Authorization: Bearer <token>`

**Response (200):**
```json
{
  "success": true
}
```

**Error Responses:**
- `403` - Permission denied
- `404` - Channel not found

### GET /channels/{channel_id}/webhooks

Get all webhooks for a channel. Requires authentication and manage webhooks permission.

**Headers:** `Authorization: Bearer <token>`

**Response (200):**
```json
[
  {
    "id": "123456789012345678",
    "channel_id": "123456789012345678",
    "server_id": "234567890123456789",
    "creator_id": "345678901234567890",
    "name": "My Webhook",
    "avatar_url": "https://cdn.example.com/avatars/webhook.png",
    "created_at": 1704067200000
  }
]
```

**Error Responses:**
- `403` - Permission denied
- `404` - Channel not found

### POST /channels/{channel_id}/invites

Create an invite for a channel. Requires create instant invite permission.

**Headers:** `Authorization: Bearer <token>`

**Request:**
```json
{
  "max_age": 86400,
  "max_uses": 0,
  "temporary": false
}
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| max_age | int | 86400 | Invite expiry in seconds (0 = never) |
| max_uses | int | 0 | Max uses (0 = unlimited) |
| temporary | bool | false | Grant temporary membership |

**Response (200):**
```json
{
  "code": "abc123",
  "channel_id": "123456789012345678",
  "server_id": "234567890123456789",
  "max_age": 86400,
  "max_uses": 0,
  "temporary": false,
  "uses": 0,
  "created_at": 1704067200000
}
```

### GET /channels/invites/{invite_code}

Get invite information. Requires authentication.

**Headers:** `Authorization: Bearer <token>`

**Response (200):**
```json
{
  "code": "abc123",
  "server_id": "123456789012345678",
  "server_name": "My Server",
  "channel_id": "234567890123456789",
  "inviter_id": "345678901234567890",
  "uses": 5,
  "max_uses": 100,
  "expires_at": 1704153600000
}
```

**Error Responses:**
- `404` - Invite not found or expired

### POST /channels/invites/{invite_code}

Join a server via invite code. Requires authentication.

**Headers:** `Authorization: Bearer <token>`

**Response (200):**
```json
{
  "success": true,
  "server_id": "123456789012345678"
}
```

**Error Responses:**
- `403` - Banned from server
- `404` - Invite not found or expired
- `409` - Already a member

### DELETE /channels/invites/{invite_code}

Delete an invite. Requires manage server permission.

**Headers:** `Authorization: Bearer <token>`

**Response (200):**
```json
{
  "success": true
}
```

### POST /channels/{channel_id}/attachments

Upload a file attachment to a channel. Requires authentication.

**Headers:** `Authorization: Bearer <token>`

**Request:** Multipart form data with `file` field

**File Size Limits:** Based on user tier:
- Standard: 10MB
- Alpha: 25MB
- Premium: 100MB

**Response (200):**
```json
{
  "id": "abc123def456",
  "filename": "image.png",
  "size": 12345,
  "content_type": "image/png",
  "url": "/api/v1/media/attachments/abc123def456"
}
```

### GET /media/attachments/{filename}

Retrieve an uploaded attachment. Requires authentication.

**Headers:** `Authorization: Bearer <token>`

**Alternative:** Pass token as query parameter: `?token=<token>`

This is useful for embedding images/videos in HTML where headers cannot be set.

**Response (200):** File content with appropriate Content-Type header

**Response (401):** Authentication required
```json
{
  "error": {
    "code": 401,
    "message": "Authentication required"
  }
}
```

---

## Message Endpoints

### GET /channels/{channel_id}/messages

Get messages in a channel. Requires authentication and access.

**Headers:** `Authorization: Bearer <token>`

**Query Parameters:**
| Parameter | Type | Default | Constraints | Description |
|-----------|------|---------|-------------|-------------|
| limit | int | 50 | 1-100 | Max messages to return |
| before | string | null | Snowflake ID | Get messages before this ID |
| after | string | null | Snowflake ID | Get messages after this ID |

**Response (200):**
```json
[
  {
    "id": "123456789012345678",
    "channel_id": "123456789012345678",
    "author_id": "123456789012345678",
    "content": "Hello, world!",
    "created_at": 1704067200000,
    "edited_at": null,
    "reply_to_id": null,
    "attachments": [],
    "embeds": [],
    "pinned": false,
    "author_username": "johndoe"
  }
]
```

**Note:** The `author_username` field is included for convenience when listing messages.

**Error Responses:**
- `403` - Access denied
- `404` - Channel not found

### POST /channels/{channel_id}/messages

Send a message to a channel. Requires authentication and send permission.

**Headers:** `Authorization: Bearer <token>`

**Request:**
```json
{
  "content": "Hello, world!",
  "reply_to_id": "123456789012345678",
  "attachments": [
    {
      "filename": "image.png",
      "content_type": "image/png",
      "size": 12345,
      "url": "https://cdn.example.com/attachments/image.png"
    }
  ],
  "embeds": []
}
```

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| content | string | Max 4000 chars, optional | Message content |
| reply_to_id | string | Snowflake ID, optional | Message to reply to |
| attachments | array | Optional | File attachments |
| embeds | array | Optional | Rich embeds |

**Note:** At least one of `content`, `attachments`, or `embeds` is required.

**Response (200):**
```json
{
  "id": "123456789012345678",
  "channel_id": "123456789012345678",
  "author_id": "123456789012345678",
  "content": "Hello, world!",
  "created_at": 1704067200000,
  "edited_at": null,
  "reply_to_id": null,
  "attachments": [],
  "embeds": [],
  "pinned": false,
  "author_username": "johndoe"
}
```

**Error Responses:**
- `400` - Empty message or invalid content
- `403` - Permission denied
- `404` - Channel not found

### GET /channels/{channel_id}/messages/{message_id}

Get a specific message. Requires authentication and access.

**Headers:** `Authorization: Bearer <token>`

**Response (200):**
```json
{
  "id": "123456789012345678",
  "channel_id": "123456789012345678",
  "author_id": "123456789012345678",
  "content": "Hello, world!",
  "created_at": 1704067200000,
  "edited_at": null,
  "reply_to_id": null,
  "attachments": [],
  "embeds": [],
  "pinned": false
}
```

**Error Responses:**
- `400` - Invalid message ID
- `403` - Access denied
- `404` - Message not found

### PATCH /channels/{channel_id}/messages/{message_id}

Edit a message. Only the author can edit.

**Headers:** `Authorization: Bearer <token>`

**Request:**
```json
{
  "content": "Updated message content"
}
```

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| content | string | Max 4000 chars | New message content |

**Response (200):**
```json
{
  "id": "123456789012345678",
  "channel_id": "123456789012345678",
  "author_id": "123456789012345678",
  "content": "Updated message content",
  "created_at": 1704067200000,
  "edited_at": 1704067300000,
  "reply_to_id": null,
  "attachments": [],
  "embeds": [],
  "pinned": false
}
```

**Error Responses:**
- `400` - Invalid content
- `403` - Not the author
- `404` - Message not found

### DELETE /channels/{channel_id}/messages/{message_id}

Delete a message. Author or moderators can delete.

**Headers:** `Authorization: Bearer <token>`

**Response (200):**
```json
{
  "success": true
}
```

**Error Responses:**
- `403` - Permission denied
- `404` - Message not found

### GET /channels/{channel_id}/messages/search

Search messages in a channel by content. Requires authentication.

**Headers:** `Authorization: Bearer <token>`

**Query Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| content | string | Yes | Search query (case-insensitive) |
| limit | int | No | Max results (1-100, default 25) |

**Response (200):**
```json
[
  {
    "id": "123456789012345678",
    "channel_id": "123456789012345678",
    "author_id": "123456789012345678",
    "content": "Message containing search term",
    "created_at": 1704067200000,
    "edited_at": null,
    "author_username": "johndoe"
  }
]
```

### GET /channels/{channel_id}/pins

Get all pinned messages in a channel. Requires authentication.

**Headers:** `Authorization: Bearer <token>`

**Response (200):**
```json
[
  {
    "id": "123456789012345678",
    "channel_id": "123456789012345678",
    "author_id": "123456789012345678",
    "content": "Important pinned message",
    "created_at": 1704067200000,
    "pinned": true,
    "author_username": "johndoe"
  }
]
```

### PUT /channels/{channel_id}/pins/{message_id}

Pin a message in a channel. Requires authentication.

**Headers:** `Authorization: Bearer <token>`

**Response (200):**
```json
{
  "success": true
}
```

**Error Responses:**
- `403` - Permission denied
- `404` - Message not found

### DELETE /channels/{channel_id}/pins/{message_id}

Unpin a message from a channel. Requires authentication.

**Headers:** `Authorization: Bearer <token>`

**Response (200):**
```json
{
  "success": true
}
```

**Error Responses:**
- `403` - Permission denied
- `404` - Message not found

### POST /channels/{channel_id}/typing

Trigger typing indicator in a channel. Requires authentication.

**Headers:** `Authorization: Bearer <token>`

**Response (200):**
```json
{
  "success": true
}
```

---

## Relationship Endpoints

### GET /relationships/@me

Get all relationships for current user. Requires authentication.

**Headers:** `Authorization: Bearer <token>`

**Response (200):**
```json
[
  {
    "user_id": "123456789012345678",
    "username": "johndoe",
    "avatar_url": "https://cdn.example.com/avatars/123.png",
    "status": "friend",
    "presence": {
      "status": "online"
    },
    "created_at": 1704067200000
  },
  {
    "user_id": "234567890123456789",
    "username": "janedoe",
    "avatar_url": null,
    "status": "pending_incoming",
    "presence": {
      "status": "offline"
    },
    "created_at": 1704067300000
  },
  {
    "user_id": "345678901234567890",
    "username": "blockeduser",
    "avatar_url": null,
    "status": "blocked",
    "presence": null,
    "created_at": 1704067400000
  }
]
```

**Note:** The response includes enriched user information (`username`, `avatar_url`, `presence`) for convenience.

**Relationship Statuses:**
| Status | Description |
|--------|-------------|
| `friend` | Mutual friendship |
| `pending_incoming` | Incoming friend request |
| `pending_outgoing` | Outgoing friend request |
| `blocked` | User is blocked |

### POST /relationships

Send a friend request. Requires authentication.

**Headers:** `Authorization: Bearer <token>`

**Request:**
```json
{
  "user_id": "123456789012345678",
  "message": "Hey, let's be friends!"
}
```

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| user_id | string | Snowflake ID | Target user ID |
| message | string | Max 256 chars, optional | Request message |

**Response (200):**
```json
{
  "user_id": "123456789012345678",
  "status": "pending_outgoing",
  "created_at": 1704067200000
}
```

**Error Responses:**
- `400` - Cannot send request to yourself
- `403` - User has blocked you
- `404` - User not found
- `409` - Request already exists or already friends

### PUT /relationships/{user_id}/accept

Accept a friend request. Requires authentication.

**Headers:** `Authorization: Bearer <token>`

**Response (200):**
```json
{
  "success": true
}
```

**Error Responses:**
- `400` - Invalid user ID
- `404` - Friend request not found

### DELETE /relationships/{user_id}

Remove a relationship (unfriend, decline request, cancel request, or unblock).

**Headers:** `Authorization: Bearer <token>`

**Response (200):**
```json
{
  "success": true
}
```

**Error Responses:**
- `400` - Invalid user ID
- `404` - Relationship not found

### POST /relationships/block

Block a user. Removes any existing relationship.

**Headers:** `Authorization: Bearer <token>`

**Request:**
```json
{
  "user_id": "123456789012345678"
}
```

**Response (200):**
```json
{
  "user_id": "123456789012345678",
  "status": "blocked",
  "created_at": 1704067200000
}
```

**Error Responses:**
- `400` - Cannot block yourself
- `404` - User not found
- `409` - User already blocked

---

## Presence Endpoints

### PUT /users/@me/presence

Update current user's presence. Requires authentication.

**Headers:** `Authorization: Bearer <token>`

**Request:**
```json
{
  "status": "online",
  "custom_status": "Working on PlexiChat",
  "custom_emoji": "💻"
}
```

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| status | string | Required | Status value |
| custom_status | string | Max 128 chars, optional | Custom status text |
| custom_emoji | string | Optional | Custom status emoji |

**Status Values:** `online`, `idle`, `dnd`, `invisible`, `offline`

**Response (200):**
```json
{
  "user_id": "123456789012345678",
  "status": "online",
  "custom_status": "Working on PlexiChat",
  "custom_emoji": "💻",
  "last_seen": 1704067200000
}
```

**Error Responses:**
- `400` - Invalid status value

### GET /users/{user_id}/presence

Get a user's presence. Requires authentication.

**Headers:** `Authorization: Bearer <token>`

**Response (200):**
```json
{
  "user_id": "123456789012345678",
  "status": "online",
  "custom_status": "Working on PlexiChat",
  "custom_emoji": "💻",
  "last_seen": 1704067200000
}
```

**Note:** Returns `offline` status if user not found or presence not visible.

---

## Reaction Endpoints

### PUT /channels/{channel_id}/messages/{message_id}/reactions/{emoji}

Add a reaction to a message. Requires authentication.

**Headers:** `Authorization: Bearer <token>`

**Response (200):**
```json
{
  "success": true
}
```

**Error Responses:**
- `400` - Invalid emoji or reaction limit reached
- `403` - Permission denied
- `404` - Message not found

### DELETE /channels/{channel_id}/messages/{message_id}/reactions/{emoji}

Remove your reaction from a message. Requires authentication.

**Headers:** `Authorization: Bearer <token>`

**Response (200):**
```json
{
  "success": true
}
```

### GET /channels/{channel_id}/messages/{message_id}/reactions

Get all reactions on a message. Requires authentication.

**Headers:** `Authorization: Bearer <token>`

**Response (200):**
```json
[
  {
    "emoji": "👍",
    "count": 5,
    "me": true
  },
  {
    "emoji": "❤️",
    "count": 3,
    "me": false
  }
]
```

**Error Responses:**
- `400` - Invalid message ID
- `404` - Message not found

### GET /channels/{channel_id}/messages/{message_id}/reactions/{emoji}

Get users who reacted with a specific emoji. Requires authentication.

**Headers:** `Authorization: Bearer <token>`

**Query Parameters:**
| Parameter | Type | Default | Constraints | Description |
|-----------|------|---------|-------------|-------------|
| limit | int | 50 | 1-100 | Max users to return |
| after | string | null | Snowflake ID | Get users after this ID |

**Response (200):**
```json
[
  {
    "user_id": "123456789012345678",
    "reacted_at": 1704067200000
  }
]
```

**Error Responses:**
- `400` - Invalid message ID
- `404` - Message not found

---

## Webhook Endpoints

### POST /webhooks

Create a new webhook. Requires authentication and manage webhooks permission.

**Headers:** `Authorization: Bearer <token>`

**Request:**
```json
{
  "channel_id": "123456789012345678",
  "name": "My Webhook",
  "avatar_url": "https://cdn.example.com/avatars/webhook.png"
}
```

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| channel_id | string | Snowflake ID | Target channel |
| name | string | 1-80 chars | Webhook name |
| avatar_url | string | Optional | Webhook avatar URL |

**Response (200):**
```json
{
  "id": "123456789012345678",
  "channel_id": "123456789012345678",
  "server_id": "123456789012345678",
  "creator_id": "123456789012345678",
  "name": "My Webhook",
  "avatar_url": "https://cdn.example.com/avatars/webhook.png",
  "token": "webhook.123456789012345678.random_token_string",
  "url": "/webhooks/123456789012345678/webhook.123456789012345678.random_token_string",
  "created_at": 1704067200000
}
```

**Note:** Token and URL are only returned on creation. The URL is relative to the API base URL.

**Error Responses:**
- `400` - Invalid input or webhook limit reached
- `403` - Permission denied
- `404` - Channel not found

### GET /webhooks/{webhook_id}

Get webhook details. Requires authentication and access.

**Headers:** `Authorization: Bearer <token>`

**Response (200):** Webhook object (without token)

**Error Responses:**
- `400` - Invalid webhook ID
- `403` - Access denied
- `404` - Webhook not found

### DELETE /webhooks/{webhook_id}

Delete a webhook. Requires authentication and manage webhooks permission.

**Headers:** `Authorization: Bearer <token>`

**Response (200):**
```json
{
  "success": true
}
```

**Error Responses:**
- `403` - Permission denied
- `404` - Webhook not found

### POST /webhooks/{webhook_id}/{token}

Execute a webhook (send a message). No authentication required if token is valid.

**Query Parameters:**
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| wait | bool | false | Wait for message creation and return message object |

**Request:**
```json
{
  "content": "Webhook message",
  "username": "Custom Name",
  "avatar_url": "https://cdn.example.com/avatars/custom.png",
  "embeds": [],
  "thread_id": "123456789012345678"
}
```

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| content | string | Max 2000 chars, optional | Message content |
| username | string | Max 80 chars, optional | Override webhook name |
| avatar_url | string | Optional | Override webhook avatar |
| embeds | array | Optional | Rich embeds |
| thread_id | string | Snowflake ID, optional | Thread to post to |

**Note:** At least one of `content` or `embeds` is required.

**Response (200 with wait=true):**
```json
{
  "id": "123456789012345678",
  "webhook_id": "123456789012345678",
  "channel_id": "123456789012345678",
  "content": "Webhook message",
  "username": "Custom Name",
  "avatar_url": "https://cdn.example.com/avatars/custom.png",
  "created_at": 1704067200000
}
```

**Response (200 with wait=false):** `null`

**Error Responses:**
- `400` - Empty message or invalid content
- `401` - Invalid webhook token
- `404` - Webhook not found

---

## User Settings Endpoints

Cloud-synced key-value store for user preferences like themes, UI settings, and other client configurations.

### Configuration Limits

| Setting | Default | Description |
|---------|---------|-------------|
| max_settings_per_user | 100 | Maximum settings per user |
| max_key_length | 100 | Maximum key length in characters |
| max_value_length | 10000 | Maximum value length in characters |

### GET /users/@me/settings

Get all settings for the current user. Requires authentication.

**Headers:** `Authorization: Bearer <token>`

**Response (200):**
```json
{
  "settings": {
    "theme": "dark",
    "sidebar_collapsed": "false",
    "notification_sound": "enabled"
  },
  "count": 3,
  "limit": 100
}
```

### GET /users/@me/settings/{key}

Get a specific setting by key. Requires authentication.

**Headers:** `Authorization: Bearer <token>`

**Response (200):**
```json
{
  "key": "theme",
  "value": "dark",
  "created_at": 1704067200000,
  "updated_at": 1704067200000
}
```

**Error Responses:**
- `404` - Setting not found

### PUT /users/@me/settings/{key}

Set a setting value (create or update). Requires authentication.

**Headers:** `Authorization: Bearer <token>`

**Request:**
```json
{
  "value": "dark"
}
```

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| value | string | Max 10000 chars | Setting value |

**Response (200):**
```json
{
  "key": "theme",
  "value": "dark",
  "created_at": 1704067200000,
  "updated_at": 1704067200000
}
```

**Error Responses:**
- `400` - Key too long, value too long, reserved key, or limit exceeded

### DELETE /users/@me/settings/{key}

Delete a setting. Requires authentication.

**Headers:** `Authorization: Bearer <token>`

**Response (200):**
```json
{
  "success": true
}
```

**Error Responses:**
- `404` - Setting not found

---

## WebSocket Gateway

Connect to the WebSocket gateway for real-time events.

### Connection URL

```
wss://gateway.example.com/gateway
```

### Opcodes

| Code | Name | Description | Direction |
|------|------|-------------|-----------|
| 0 | DISPATCH | Event dispatch | Server → Client |
| 1 | HEARTBEAT | Keep connection alive | Bidirectional |
| 2 | IDENTIFY | Authenticate connection | Client → Server |
| 3 | PRESENCE_UPDATE | Update presence | Client → Server |
| 4 | VOICE_STATE_UPDATE | Update voice state | Client → Server |
| 6 | RESUME | Resume session | Client → Server |
| 7 | RECONNECT | Server requests reconnect | Server → Client |
| 8 | REQUEST_GUILD_MEMBERS | Request member list | Client → Server |
| 9 | INVALID_SESSION | Session invalidated | Server → Client |
| 10 | HELLO | Initial handshake | Server → Client |
| 11 | HEARTBEAT_ACK | Heartbeat acknowledged | Server → Client |
| 12 | SERVER_STATUS | Server status update | Server → Client |
| 13 | VERSION_CHECK | Version compatibility check | Bidirectional |
| 20 | VOICE_CONNECT | Voice connection request | Client → Server |
| 21 | VOICE_DISCONNECT | Voice disconnection | Client → Server |
| 22 | VOICE_SDP_OFFER | WebRTC SDP offer | Bidirectional |
| 23 | VOICE_SDP_ANSWER | WebRTC SDP answer | Bidirectional |
| 24 | VOICE_ICE_CANDIDATE | WebRTC ICE candidate | Bidirectional |
| 25 | VOICE_SPEAKING | Speaking indicator | Bidirectional |
| 26 | VOICE_QUALITY | Voice quality metrics | Server → Client |
| 30 | INTERACTION_CREATE | Application interaction | Server → Client |
| 31 | INTERACTION_RESPONSE | Interaction response | Client → Server |

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
| 4010 | INVALID_SHARD | Invalid shard | No |
| 4011 | SHARDING_REQUIRED | Sharding required | No |
| 4012 | INVALID_API_VERSION | Invalid API version | No |
| 4013 | INVALID_INTENTS | Invalid intents | No |
| 4014 | DISALLOWED_INTENTS | Disallowed intents | No |
| 4015 | VERSION_OUTDATED | Client version outdated | No |
| 4016 | SERVER_MAINTENANCE | Server maintenance | Yes (after maintenance) |
| 4017 | SERVER_SHUTDOWN | Server shutting down | Yes (after restart) |

### Connection Flow

1. Connect to WebSocket endpoint
2. Receive HELLO (opcode 10) with heartbeat interval
3. Send IDENTIFY (opcode 2) with token
4. Receive DISPATCH with READY event
5. Send HEARTBEAT (opcode 1) at specified interval
6. Receive HEARTBEAT_ACK (opcode 11) for each heartbeat

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

## Rate Limits

PlexiChat uses multiple rate limiting algorithms depending on the endpoint.

### Rate Limit Algorithms

| Algorithm | Description |
|-----------|-------------|
| Token Bucket | Allows bursts, refills over time |
| Sliding Window | Smooth rate limiting over time window |
| Fixed Window | Simple count per time window |

### Default Limits

| Endpoint Category | Requests | Window | Burst | Algorithm |
|-------------------|----------|--------|-------|-----------|
| Global (per user) | 120 | 60s | 20 | Sliding Window |
| Global (per second) | 50 | 1s | 10 | Token Bucket |
| POST /auth/login | 5 | 60s | 0 | Fixed Window |
| POST /auth/register | 3 | 60s | 0 | Fixed Window |
| POST /auth/2fa | 5 | 60s | 2 | Sliding Window |
| POST /channels/{id}/messages | 5 | 5s | 3 | Token Bucket |
| PATCH/DELETE messages | 5 | 5s | 2 | Sliding Window |
| PUT/DELETE reactions | 1 | 0.25s | 1 | Token Bucket |
| PATCH /users/@me | 2 | 60s | 0 | Fixed Window |
| POST /servers | 10 | 60s | 2 | Sliding Window |
| DELETE /servers/{id} | 1 | 60s | 0 | Fixed Window |
| POST /relationships | 5 | 60s | 2 | Sliding Window |
| POST /webhooks | 5 | 60s | 2 | Sliding Window |
| POST /webhooks/{id}/{token} | 5 | 2s | 5 | Token Bucket |
| GET /channels/{id}/messages | 10 | 10s | 5 | Sliding Window |
| GET /servers/{id} | 20 | 10s | 10 | Sliding Window |
| GET /users/@me | 30 | 60s | 10 | Sliding Window |

### Hourly/Daily Limits

Some endpoints have additional hourly or daily limits:

| Endpoint | Hourly Limit | Daily Limit |
|----------|--------------|-------------|
| POST /auth/login | 20 | - |
| POST /auth/register | 10 | 20 |
| PATCH /users/@me | 10 | - |
| POST /servers | - | 100 |
| POST /relationships | 50 | - |

### Rate Limit Headers

All responses include rate limit headers:

```
X-RateLimit-Limit: 50
X-RateLimit-Remaining: 49
X-RateLimit-Reset: 1704067200
X-RateLimit-Bucket: route:POST:/channels/{id}/messages
```

### Rate Limit Response (HTTP 429)

```json
{
  "error": {
    "code": 429,
    "message": "Rate limited",
    "retry_after": 1.5
  }
}
```

### Bot Rate Limits

Bots receive a 1.2x multiplier on certain high-traffic routes:
- POST /channels/{id}/messages
- GET /channels/{id}/messages
- PUT/DELETE reactions

### Bypassing Rate Limits

Internal requests can bypass rate limits using headers:
- `X-Internal-Request: true`
- `X-RateLimit-Bypass: <key>`

Admins with `admin.*` or `*` permissions are also exempt.

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

### Graceful Shutdown Handling

When the server shuts down, clients receive notifications in this order:

1. **SERVER_STATUS (opcode 12)** - Sent to all connected clients with shutdown details
2. **Grace period** - Server waits ~2 seconds for clients to prepare
3. **Connection close** - WebSocket closed with code 4017 (SERVER_SHUTDOWN)

Clients should:
1. Listen for opcode 12 messages during the connection
2. On receiving `state: "shutting_down"`, save any pending state
3. Display the shutdown message to users
4. After disconnection, poll `/api/v1/status` to detect when server is back
5. Reconnect with exponential backoff once status returns to `running`

### Error Handling

Always check for version-related error codes:
- `VERSION_OUTDATED`: Client must update
- `INVALID_VERSION_FORMAT`: Client sent malformed version string

---

## Data Types

### Snowflake ID

All IDs in PlexiChat are snowflake IDs - 64-bit integers represented as strings in JSON.

```
123456789012345678
```

Snowflake structure:
- Bits 63-22: Timestamp (milliseconds since epoch 2024-01-01)
- Bits 21-17: Datacenter ID
- Bits 16-12: Worker ID
- Bits 11-0: Sequence number

### Timestamps

All timestamps are Unix timestamps in milliseconds (integer).

```json
{
  "created_at": 1704067200000
}
```

**Note:** Some endpoints (like user settings) may return timestamps in seconds. Check the specific endpoint documentation for details.

### Pagination

List endpoints support cursor-based pagination:

```
GET /channels/{id}/messages?limit=50&before=123456789012345678
GET /channels/{id}/messages?limit=50&after=123456789012345678
```

- `before`: Get items with ID less than this value
- `after`: Get items with ID greater than this value
- `limit`: Maximum items to return (1-100, default 50)

---

## Telemetry Endpoints

### POST /telemetry/response-times

Submit anonymized response time telemetry data. Clients can batch up to 100 entries per submission.

**Request:**
```json
{
  "entries": [
    {
      "endpoint": "/api/v1/users/@me",
      "method": "GET",
      "response_time_ms": 45.2,
      "status_code": 200,
      "timestamp": 1704067200000
    }
  ]
}
```

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| entries | array | Max 100 items | List of response time measurements |
| entries[].endpoint | string | Max 255 chars | API endpoint path |
| entries[].method | string | Max 10 chars | HTTP method |
| entries[].response_time_ms | float | >= 0 | Response time in milliseconds |
| entries[].status_code | int | 100-599 | HTTP status code |
| entries[].timestamp | int | Optional | Unix timestamp in milliseconds |

**Response (200):**
```json
{
  "accepted": 5,
  "message": "Accepted 5 of 5 entries"
}
```

**Error Responses:**
- `429` - Rate limited (max 10 submissions per minute)
- `503` - Telemetry collection disabled

---

## Admin Endpoints

Admin endpoints are restricted to localhost by default and require admin authentication.

### GET /admin

Serve the admin login page. Only accessible from allowed hosts.

**Response:** HTML login page

### GET /admin/ui

Serve the admin dashboard UI. Only accessible from allowed hosts.

**Response:** HTML dashboard page (requires valid admin token in localStorage)

### POST /admin/login

Authenticate as admin.

**Request:**
```json
{
  "username": "admin",
  "password": "your_password"
}
```

**Response (Success - OTP disabled):**
```json
{
  "status": "success",
  "token": "admin_session_token"
}
```

**Response (OTP Setup Required - first login):**
```json
{
  "status": "otp_setup_required",
  "admin_id": "123456789012345678",
  "otp_secret": "JBSWY3DPEHPK3PXP",
  "otp_qr_uri": "otpauth://totp/PlexiChat%20Admin:admin?secret=...",
  "message": "Scan the QR code with your authenticator app, then enter the code"
}
```

**Response (OTP Verification Required):**
```json
{
  "status": "otp_required",
  "admin_id": "123456789012345678",
  "message": "Enter your 2FA code"
}
```

### POST /admin/verify-otp

Verify OTP code for admin login.

**Request:**
```json
{
  "admin_id": "123456789012345678",
  "code": "123456",
  "is_setup": false
}
```

| Field | Type | Description |
|-------|------|-------------|
| admin_id | string | Admin user ID (as string to avoid JS precision loss) |
| code | string | 6-digit TOTP code or backup code |
| is_setup | bool | True if this is initial OTP setup |

**Response (200):**
```json
{
  "status": "success",
  "token": "admin_session_token"
}
```

### POST /admin/logout

Logout admin session.

**Headers:** `Authorization: Bearer <admin_token>`

**Response (200):**
```json
{
  "success": true
}
```

### GET /admin/dashboard

Get admin dashboard summary data.

**Headers:** `Authorization: Bearer <admin_token>`

**Response (200):**
```json
{
  "tickets": {
    "open": 5,
    "in_progress": 2,
    "resolved": 10,
    "closed": 3,
    "total": 20
  },
  "telemetry": [
    {
      "endpoint": "/api/v1/users/@me",
      "method": "GET",
      "count": 1000,
      "avg_ms": 45.2,
      "p95_ms": 120.5,
      "error_rate": 0.5
    }
  ]
}
```

### GET /admin/tickets

Get feedback tickets with optional filtering.

**Headers:** `Authorization: Bearer <admin_token>`

**Query Parameters:**
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| status_filter | string | null | Filter by status (open, in_progress, resolved, closed) |
| limit | int | 50 | Max tickets to return |
| offset | int | 0 | Pagination offset |

**Response (200):**
```json
[
  {
    "id": 123456789012345678,
    "user_id": 234567890123456789,
    "username": "johndoe",
    "content": "Feature request...",
    "category": "feature",
    "rating": 4,
    "status": "open",
    "created_at": 1704067200000,
    "resolved_at": null,
    "resolved_by": null
  }
]
```

### GET /admin/tickets/{ticket_id}

Get a single ticket by ID.

**Headers:** `Authorization: Bearer <admin_token>`

**Response (200):** Ticket object

### PATCH /admin/tickets/{ticket_id}/status

Update ticket status.

**Headers:** `Authorization: Bearer <admin_token>`

**Request:**
```json
{
  "status": "resolved"
}
```

| Field | Type | Values | Description |
|-------|------|--------|-------------|
| status | string | open, in_progress, resolved, closed | New status |

**Response (200):**
```json
{
  "success": true,
  "status": "resolved"
}
```

### GET /admin/tickets/{ticket_id}/notes

Get internal notes for a ticket.

**Headers:** `Authorization: Bearer <admin_token>`

**Response (200):**
```json
[
  {
    "id": 123456789012345678,
    "ticket_id": 234567890123456789,
    "admin_id": 345678901234567890,
    "admin_username": "admin",
    "content": "Contacted user via email",
    "created_at": 1704067200000
  }
]
```

### POST /admin/tickets/{ticket_id}/notes

Add internal note to a ticket.

**Headers:** `Authorization: Bearer <admin_token>`

**Request:**
```json
{
  "content": "Contacted user via email"
}
```

**Response (200):** Note object

### GET /admin/telemetry/stats

Get telemetry statistics.

**Headers:** `Authorization: Bearer <admin_token>`

**Query Parameters:**
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| hours | int | 24 | Hours to look back |
| endpoint | string | null | Filter by endpoint pattern |

**Response (200):**
```json
{
  "stats": [
    {
      "endpoint": "/api/v1/users/@me",
      "method": "GET",
      "count": 1000,
      "avg_ms": 45.2,
      "min_ms": 10.0,
      "max_ms": 500.0,
      "p50_ms": 40.0,
      "p95_ms": 120.5,
      "p99_ms": 200.0,
      "error_rate": 0.5
    }
  ]
}
```

### GET /admin/telemetry/history

Get response time history for an endpoint.

**Headers:** `Authorization: Bearer <admin_token>`

**Query Parameters:**
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| endpoint | string | Required | API endpoint path |
| method | string | GET | HTTP method |
| hours | int | 24 | Hours to look back |
| bucket_minutes | int | 5 | Time bucket size in minutes |

**Response (200):**
```json
{
  "history": [
    {
      "timestamp": 1704067200000,
      "avg_response_time_ms": 45.2,
      "count": 100,
      "min_response_time_ms": 10.0,
      "max_response_time_ms": 200.0
    }
  ]
}
```

---

## Configuration

### Admin UI Configuration

```yaml
admin_ui:
  enabled: true
  path: /admin
  # Set to false to disable 2FA requirement for admin login
  # WARNING: Disabling OTP reduces security significantly!
  require_otp: true
  host_restriction:
    # WARNING: Disabling this allows ANYONE to access the admin panel!
    enabled: true
    allowed_hosts:
      - 127.0.0.1
      - localhost
      - "::1"
  # Allowed origins for admin panel CORS (empty = use main api.cors_origins)
  allowed_origins: []
  # Rate limiting for admin login attempts
  rate_limit:
    max_attempts: 5       # Max login attempts before lockout
    window_seconds: 300   # Time window for attempts (5 min)
    lockout_seconds: 900  # Lockout duration (15 min)
```

**First-Time Setup:**
1. Start the server - admin credentials are auto-generated
2. Find credentials in `~/.plexichat/admin_credentials.txt`
3. Access admin panel at `http://localhost:8000/api/v1/admin`
4. Login and set up 2FA (if `require_otp: true`)
5. Delete the credentials file after noting them

### Telemetry Configuration

```yaml
telemetry:
  enabled: true
  rate_limit:
    max_per_minute: 10
  retention_days: 30
```

### TLS Configuration

```yaml
tls:
  enabled: false
  auto_generate_self_signed: false
  cert_path: ~/.plexichat/certs/server.crt
  key_path: ~/.plexichat/certs/server.key
  cert_days: 365
```
