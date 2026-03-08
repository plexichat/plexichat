# Presence API

Endpoints for managing user presence and status.

## PUT /users/@me/presence

Update the current user's presence status.

### Headers

```
Authorization: Bearer <token>
```

### Request Body

| Field | Type | Required | Constraints | Description |
|-------|------|----------|-------------|-------------|
| status | string | Yes | Valid status | Online status |
| custom_status | string | No | Max 128 chars | Custom status text |
| custom_emoji | string | No | - | Custom status emoji |

### Status Values

| Status | Description |
|--------|-------------|
| online | User is online |
| idle | User is idle/away |
| dnd | Do not disturb |
| invisible | Appear offline to others |
| offline | Go offline |

### Example Request

```json
{
  "status": "online",
  "custom_status": "Working on PlexiChat",
  "custom_emoji": ":computer:"
}
```

### Response (200 OK)

```json
{
  "user_id": "123456789012345678",
  "status": "online",
  "custom_status": "Working on PlexiChat",
  "custom_emoji": ":computer:",
  "last_seen": 1704067200
}
```

### Error Responses

| Status | Code | Description |
|--------|------|-------------|
| 400 | Invalid status | Status value not recognized |

## GET /users/{user_id}/presence

Get a user's presence status.

### Headers

```
Authorization: Bearer <token>
```

### Path Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| user_id | string | User's snowflake ID |

### Response (200 OK)

```json
{
  "user_id": "123456789012345678",
  "status": "online",
  "custom_status": "Working on PlexiChat",
  "custom_emoji": ":computer:",
  "last_seen": 1704067200
}
```

### Notes

- Returns `offline` status if user not found
- Returns `offline` if user's presence is not visible to requester
- `invisible` users appear as `offline` to others

## Presence Object

```json
{
  "user_id": "123456789012345678",
  "status": "online",
  "custom_status": "Working on PlexiChat",
  "custom_emoji": ":computer:",
  "last_seen": 1704067200
}
```

| Field | Type | Description |
|-------|------|-------------|
| user_id | string | User's snowflake ID |
| status | string | Current status |
| custom_status | string? | Custom status text |
| custom_emoji | string? | Custom status emoji |
| last_seen | int? | Unix timestamp of last activity |
