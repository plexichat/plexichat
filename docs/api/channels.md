# Channels API

Endpoints for channel management.

## GET /channels/{channel_id}

Get channel details. Requires access to the channel.

### Headers

```
Authorization: Bearer <token>
```

### Path Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| channel_id | string | Channel's snowflake ID |

### Response (200 OK)

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
  "created_at": 1704067200
}
```

### Error Responses

| Status | Code | Description |
|--------|------|-------------|
| 400 | Invalid channel ID | ID format invalid |
| 403 | Access denied | No permission to view |
| 404 | Channel not found | Channel doesn't exist |

## PATCH /channels/{channel_id}

Update channel settings. Requires manage channels permission.

### Headers

```
Authorization: Bearer <token>
```

### Request Body

| Field | Type | Required | Constraints | Description |
|-------|------|----------|-------------|-------------|
| name | string | No | 1-100 characters | Channel name |
| topic | string | No | Max 1024 characters | Channel topic |
| position | int | No | >= 0 | Channel position |
| nsfw | bool | No | - | NSFW flag |
| slowmode_seconds | int | No | 0-21600 | Slowmode delay in seconds |

### Example Request

```json
{
  "name": "updated-channel",
  "topic": "New topic",
  "slowmode_seconds": 5
}
```

### Response (200 OK)

Returns the updated channel object.

### Error Responses

| Status | Code | Description |
|--------|------|-------------|
| 403 | Permission denied | Missing manage channels permission |
| 404 | Channel not found | Channel doesn't exist |

## DELETE /channels/{channel_id}

Delete a channel. Requires manage channels permission.

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
| 403 | Permission denied | Missing manage channels permission |
| 404 | Channel not found | Channel doesn't exist |

## GET /channels/{channel_id}/webhooks

Get all webhooks for a channel. Requires manage webhooks permission.

### Headers

```
Authorization: Bearer <token>
```

### Response (200 OK)

```json
[
  {
    "id": "123456789012345678",
    "channel_id": "123456789012345678",
    "server_id": "234567890123456789",
    "creator_id": "345678901234567890",
    "name": "My Webhook",
    "avatar_url": "https://cdn.example.com/avatars/webhook.png",
    "created_at": 1704067200
  }
]
```

### Error Responses

| Status | Code | Description |
|--------|------|-------------|
| 403 | Permission denied | Missing manage webhooks permission |
| 404 | Channel not found | Channel doesn't exist |

## Channel Object

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
  "created_at": 1704067200
}
```

| Field | Type | Description |
|-------|------|-------------|
| id | string | Channel's snowflake ID |
| server_id | string | Parent server's ID |
| name | string | Channel name |
| channel_type | string | Channel type (text, voice, category) |
| topic | string? | Channel topic/description |
| position | int | Display position |
| category_id | string? | Parent category ID |
| nsfw | bool | NSFW content flag |
| slowmode_seconds | int | Slowmode delay (0 = disabled) |
| created_at | int | Unix timestamp of creation |

## Channel Types

| Type | Description |
|------|-------------|
| text | Text channel for messages |
| voice | Voice channel for audio communication |
| category | Category for organizing channels |

## Slowmode

Slowmode limits how often users can send messages in a channel.

| Value | Description |
|-------|-------------|
| 0 | Disabled |
| 1-21600 | Seconds between messages |

Maximum slowmode: 6 hours (21600 seconds)
