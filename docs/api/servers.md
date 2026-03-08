# Servers API

Endpoints for server (guild) management.

**Base URL**: `{{BASE_URL}}`

## GET /servers

Get all servers the authenticated user is a member of.

### Example Request

```bash
curl -X GET {{BASE_URL}}/servers \
  -H "Authorization: Bearer YOUR_SESSION_TOKEN"
```

### Response (200 OK)

```json
[
  {
    "id": "123456789012345678",
    "name": "My Server",
    "description": "A cool server",
    "icon_url": "{{BASE_URL}}/avatars/servers/123456789012345678",
    "owner_id": "123456789012345678",
    "member_count": 150,
    "default_channel_id": "234567890123456789",
    "created_at": 1704067200
  }
]
```

## POST /servers

Create a new server. The authenticated user becomes the owner.

### Request Body

| Field | Type | Required | Constraints | Description |
|-------|------|----------|-------------|-------------|
| name | string | Yes | 2-100 characters | Server name |
| description | string | No | Max 1000 characters | Server description |

### Example Request

```bash
curl -X POST {{BASE_URL}}/servers \
  -H "Authorization: Bearer YOUR_SESSION_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "My New Server",
    "description": "A place for friends"
  }'
```

### Response (200 OK)

Returns the created server object.

### Error Responses

| Status | Code | Description |
|--------|------|-------------|
| 400 | Server limit | User has reached server creation limit |

## GET /servers/{server_id}

Get server details. Requires membership.

### Headers

```
Authorization: Bearer <token>
```

### Path Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| server_id | string | Server's snowflake ID |

### Response (200 OK)

```json
{
  "id": "123456789012345678",
  "name": "My Server",
  "description": "A cool server",
  "icon_url": "https://cdn.example.com/icons/123.png",
  "owner_id": "123456789012345678",
  "member_count": 150,
  "default_channel_id": "234567890123456789",
  "created_at": 1704067200
}
```

### Error Responses

| Status | Code | Description |
|--------|------|-------------|
| 400 | Invalid server ID | ID format invalid |
| 403 | Access denied | Not a member |
| 404 | Server not found | Server doesn't exist |

## PATCH /servers/{server_id}

Update server settings. Requires manage server permission.

### Headers

```
Authorization: Bearer <token>
```

### Request Body

| Field | Type | Required | Constraints | Description |
|-------|------|----------|-------------|-------------|
| name | string | No | 2-100 characters | Server name |
| description | string | No | Max 1000 characters | Server description |

### Example Request

```json
{
  "name": "Updated Server Name",
  "description": "New description"
}
```

### Response (200 OK)

Returns the updated server object.

### Error Responses

| Status | Code | Description |
|--------|------|-------------|
| 403 | Permission denied | Missing manage server permission |
| 404 | Server not found | Server doesn't exist |

## DELETE /servers/{server_id}

Delete a server. Only the owner can delete.

### Headers

```
Authorization: Bearer <token>
```

### Response (200 OK)

```json
{
  "success": true
}
```

### Error Responses

| Status | Code | Description |
|--------|------|-------------|
| 403 | Not owner | Only owner can delete |
| 404 | Server not found | Server doesn't exist |

## POST /servers/{server_id}/icon

Upload a server icon.

### Headers

```
Authorization: Bearer <token>
Content-Type: multipart/form-data
```

### Request Body

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| file | file | Yes | Image file (JPEG, PNG, GIF, WebP) |

### Response (200 OK)

Returns the updated server object with new icon_url.

### Error Responses

| Status | Code | Description |
|--------|------|-------------|
| 400 | Invalid file type | Must be JPEG, PNG, GIF, or WebP |
| 400 | File too large | Exceeds size limit (default 2MB) |
| 403 | Permission denied | Missing manage server permission |

---

## Channels

## GET /servers/{server_id}/channels

Get all channels in a server. Returns channels the user has access to view.

### Headers

```
Authorization: Bearer <token>
```

### Response (200 OK)

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
    "created_at": 1704067200
  }
]
```

### Channel Types

| Type | Description |
|------|-------------|
| text | Text channel for messages |
| voice | Voice channel for audio |
| category | Category for organizing channels |

### Error Responses

| Status | Code | Description |
|--------|------|-------------|
| 403 | Access denied | Not a member |
| 404 | Server not found | Server doesn't exist |

## POST /servers/{server_id}/channels

Create a channel in a server. Requires manage channels permission.

### Headers

```
Authorization: Bearer <token>
```

### Request Body

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| name | string | Yes | Channel name (1-100 characters) |
| type | string | No | Channel type (text, voice, category) |
| topic | string | No | Channel topic |
| category_id | string | No | Parent category ID |
| nsfw | bool | No | NSFW flag |

### Example Request

```json
{
  "name": "announcements",
  "type": "text",
  "topic": "Important announcements"
}
```

### Response (200 OK)

Returns the created channel object.

---

## Members

## GET /servers/{server_id}/members

Get members of a server. Requires membership.

### Headers

```
Authorization: Bearer <token>
```

### Query Parameters

| Parameter | Type | Default | Constraints | Description |
|-----------|------|---------|-------------|-------------|
| limit | int | 100 | 1-1000 | Max members to return |
| after | string | null | Snowflake ID | Get members after this user ID |

### Response (200 OK)

```json
[
  {
    "user_id": "123456789012345678",
    "username": "johndoe",
    "avatar_url": "https://cdn.example.com/avatars/123.png",
    "nickname": "John",
    "roles": ["234567890123456789"],
    "joined_at": 1704067200,
    "presence": {
      "status": "online"
    }
  }
]
```

### Response Fields

| Field | Type | Description |
|-------|------|-------------|
| user_id | string | Member's user ID |
| username | string | Member's username |
| avatar_url | string? | Member's avatar URL |
| nickname | string? | Server-specific nickname |
| roles | array | Array of role IDs |
| joined_at | int | Unix timestamp of join |
| presence | object | User's presence status |

## DELETE /servers/{server_id}/members/{member_id}

Kick a member from a server. Requires kick members permission.

### Headers

```
Authorization: Bearer <token>
```

### Response (200 OK)

```json
{
  "success": true
}
```

### Error Responses

| Status | Code | Description |
|--------|------|-------------|
| 403 | Permission denied | Missing kick permission or hierarchy violation |
| 404 | Member not found | Member doesn't exist |

## PUT /servers/{server_id}/members/{member_id}/roles/{role_id}

Assign a role to a member. Requires manage roles permission.

### Headers

```
Authorization: Bearer <token>
```

### Response (200 OK)

```json
{
  "success": true
}
```

## DELETE /servers/{server_id}/members/{member_id}/roles/{role_id}

Remove a role from a member. Requires manage roles permission.

### Headers

```
Authorization: Bearer <token>
```

### Response (200 OK)

```json
{
  "success": true
}
```

---

## Roles

## GET /servers/{server_id}/roles

Get all roles in a server.

### Headers

```
Authorization: Bearer <token>
```

### Response (200 OK)

```json
[
  {
    "id": "123456789012345678",
    "server_id": "123456789012345678",
    "name": "Admin",
    "color": "#ff0000",
    "position": 1,
    "permissions": {},
    "hoist": true,
    "mentionable": true,
    "is_default": false
  }
]
```

## POST /servers/{server_id}/roles

Create a new role. Requires manage roles permission.

### Headers

```
Authorization: Bearer <token>
```

### Request Body

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| name | string | No | Role name (default: "New Role") |
| color | string | No | Hex color code in `#RRGGBB` format. |
| permissions | object | No | Permission flags |
| hoist | bool | No | Display separately in member list |
| mentionable | bool | No | Allow @mentions |

### Example Request

```json
{
  "name": "Moderator",
  "color": "#00ff00",
  "hoist": true,
  "mentionable": true
}
```

### Response (200 OK)

Returns the created role object.

## PATCH /servers/{server_id}/roles/{role_id}

Update a role. Requires manage roles permission.

### Headers

```
Authorization: Bearer <token>
```

### Request Body

Same fields as `POST`; all fields are optional. `color` must match `#RRGGBB`.

### Response (200 OK)

Returns the updated role object.

## DELETE /servers/{server_id}/roles/{role_id}

Delete a role. Requires manage roles permission. Cannot delete the default @everyone role.

### Headers

```
Authorization: Bearer <token>
```

### Response (200 OK)

```json
{
  "success": true
}
```

---

## Bans

## GET /servers/{server_id}/bans

Get all bans in a server. Requires ban members permission.

### Headers

```
Authorization: Bearer <token>
```

### Response (200 OK)

```json
[
  {
    "user_id": "123456789012345678",
    "reason": "Spam",
    "banned_by": "234567890123456789",
    "banned_at": 1704067200
  }
]
```

## PUT /servers/{server_id}/bans/{user_id}

Ban a user from a server. Requires ban members permission.

### Headers

```
Authorization: Bearer <token>
```

### Request Body

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| reason | string | No | Ban reason |
| delete_message_days | int | No | Days of messages to delete (0-7) |

### Example Request

```json
{
  "reason": "Violating server rules",
  "delete_message_days": 1
}
```

### Response (200 OK)

```json
{
  "success": true
}
```

## DELETE /servers/{server_id}/bans/{user_id}

Unban a user from a server. Requires ban members permission.

### Headers

```
Authorization: Bearer <token>
```

### Response (200 OK)

```json
{
  "success": true
}
```

---

## Invites

## GET /servers/{server_id}/invites

Get all invites for a server. Requires manage server permission.

### Headers

```
Authorization: Bearer <token>
```

### Response (200 OK)

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
    "created_at": 1704067200,
    "expires_at": 1704153600
  }
]
```

---

## POST /servers/{server_id}/leave

Leave a server. Owners cannot leave their own server.

### Headers

```
Authorization: Bearer <token>
```

### Response (200 OK)

```json
{
  "success": true
}
```

### Error Responses

| Status | Code | Description |
|--------|------|-------------|
| 403 | Owners cannot leave | You must transfer ownership first |
| 404 | Server not found | Server doesn't exist |

## GET /servers/{server_id}/permissions

Get your current permissions in the server.

### Headers

```
Authorization: Bearer <token>
```

### Response (200 OK)

```json
{
  "server.view": true,
  "channel.create": false,
  "message.send": true,
  "message.manage": false,
  "server.manage": false
}
```

---

## Audit Log

## GET /servers/{server_id}/audit-logs

Get audit log entries for a server. Requires view audit log permission.

### Headers

```
Authorization: Bearer <token>
```

### Query Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| limit | int | 50 | Max entries to return |

### Response (200 OK)

```json
[
  {
    "id": "123456789012345678",
    "server_id": "123456789012345678",
    "user_id": "234567890123456789",
    "action": "MEMBER_KICK",
    "target_type": "member",
    "target_id": "345678901234567890",
    "changes": {},
    "reason": "Spam",
    "created_at": 1704067200
  }
]
```

---

## Webhooks

## GET /servers/{server_id}/webhooks

Get all webhooks in a server. Requires manage webhooks permission.

### Headers

```
Authorization: Bearer <token>
```

### Response (200 OK)

```json
[
  {
    "id": "123456789012345678",
    "channel_id": "234567890123456789",
    "server_id": "123456789012345678",
    "creator_id": "345678901234567890",
    "name": "My Webhook",
    "avatar_url": "https://cdn.example.com/avatars/webhook.png",
    "created_at": 1704067200
  }
]
```

---

## Server Object

```json
{
  "id": "123456789012345678",
  "name": "My Server",
  "description": "A cool server",
  "icon_url": "https://cdn.example.com/icons/123.png",
  "owner_id": "123456789012345678",
  "member_count": 150,
  "default_channel_id": "234567890123456789",
  "created_at": 1704067200
}
```

| Field | Type | Description |
|-------|------|-------------|
| id | string | Server's snowflake ID |
| name | string | Server name |
| description | string? | Server description |
| icon_url | string? | Server icon URL |
| owner_id | string | Owner's user ID |
| member_count | int | Number of members |
| default_channel_id | string? | Default channel ID |
| verification_level | int | Verification level required to join |
| default_message_notifications | int | Default notification level (0=all, 1=mentions) |
| created_at | int | Unix timestamp of creation |

---

## Related Endpoints

- [Channels](channels.md) - Channel management
- [Messages](messages.md) - Message operations
- [Webhooks](webhooks.md) - Webhook management
