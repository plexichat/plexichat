# Presence API

Endpoints for managing user presence and status.

**Base URL**: `https://api.plexichat.com/api/v1`

For development, use `http://localhost:8000/api/v1`.

All endpoints in this document are prefixed with `/api/v1/` unless otherwise specified.

## PUT /users/@me/presence

Update the current user's presence status.

### Headers

```
Authorization: Bearer <token>
```

### Request Body

- `status` (string, required, Valid status): Online status
- `custom_status` (string, optional, Max 128 chars): Custom status text
- `custom_emoji` (string, optional): Custom status emoji

### Status Values

- online: User is online
- idle: User is idle/away
- dnd: Do not disturb
- invisible: Appear offline to others
- offline: Go offline

### Example Request

```json
{
  "status": "online",
  "custom_status": "Working on Plexichat",
  "custom_emoji": ":computer:"
}
```

### Response (200 OK)

```json
{
  "user_id": "123456789012345678",
  "status": "online",
  "custom_status": "Working on Plexichat",
  "custom_emoji": ":computer:",
  "last_seen": 1704067200
}
```

### Error Responses

- 400 Invalid status: Status value not recognized

## GET /users/{user_id}/presence

Get a user's presence status.

### Headers

```
Authorization: Bearer <token>
```

### Path Parameters

- `user_id` (string): User's snowflake ID

### Response (200 OK)

```json
{
  "user_id": "123456789012345678",
  "status": "online",
  "custom_status": "Working on Plexichat",
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
  "custom_status": "Working on Plexichat",
  "custom_emoji": ":computer:",
  "last_seen": 1704067200
}
```

- `user_id` (string): User's snowflake ID
- `status` (string): Current status
- `custom_status` (string?): Custom status text
- `custom_emoji` (string?): Custom status emoji
- `last_seen` (int?): Unix timestamp of last activity
