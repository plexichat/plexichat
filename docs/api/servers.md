# Servers API

Endpoints for server (guild) management.

## GET /servers

Get all servers the authenticated user is a member of.

### Headers

```
Authorization: Bearer <token>
```

### Response (200 OK)

```json
[
  {
    "id": "123456789012345678",
    "name": "My Server",
    "description": "A cool server",
    "icon_url": "https://cdn.example.com/icons/123.png",
    "owner_id": "123456789012345678",
    "member_count": 150,
    "created_at": 1704067200
  }
]
```

## POST /servers

Create a new server. The authenticated user becomes the owner.

### Headers

```
Authorization: Bearer <token>
```

### Request Body

| Field | Type | Required | Constraints | Description |
|-------|------|----------|-------------|-------------|
| name | string | Yes | 2-100 characters | Server name |
| description | string | No | Max 1000 characters | Server description |
| icon_url | string | No | Valid URL | Server icon URL |

### Example Request

```json
{
  "name": "My New Server",
  "description": "A place for friends"
}
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
| icon_url | string | No | Valid URL | Server icon URL |

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

## Server Object

```json
{
  "id": "123456789012345678",
  "name": "My Server",
  "description": "A cool server",
  "icon_url": "https://cdn.example.com/icons/123.png",
  "owner_id": "123456789012345678",
  "member_count": 150,
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
| created_at | int | Unix timestamp of creation |
