# Reactions API

Endpoints for managing message reactions.

**Base URL**: `https://api.plexichat.com/api/v1`

For development, use `http://localhost:8000/api/v1`.

All endpoints in this document are prefixed with `/api/v1/` unless otherwise specified.

## PUT /api/v1/channels/{channel_id}/messages/{message_id}/reactions/{emoji}

Add a reaction to a message.

### Headers

```
Authorization: Bearer <token>
```

### Path Parameters

- `channel_id` (string): Channel's snowflake ID
- `message_id` (string): Message's snowflake ID
- `emoji` (string): Emoji (Unicode or custom ID)

### Response (200 OK)

```json
{
  "success": true
}
```

### Error Responses

- 400 Invalid emoji: Emoji not recognized
- 400 Reaction limit: Too many reactions on message
- 403 Permission denied: No add reactions permission
- 404 Message not found: Message doesn't exist

## DELETE /channels/{channel_id}/messages/{message_id}/reactions/{emoji}

Remove your reaction from a message.

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

The endpoint returns a successful response regardless of whether the reaction previously existed, ensuring idempotent behavior for reaction removal operations.

## GET /channels/{channel_id}/messages/{message_id}/reactions

Get all reactions on a message.

### Headers

```
Authorization: Bearer <token>
```

### Response (200 OK)

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

### Error Responses

- 400 Invalid message ID: ID format invalid
- 404 Message not found: Message doesn't exist

## GET /channels/{channel_id}/messages/{message_id}/reactions/{emoji}

Get users who reacted with a specific emoji.

### Headers

```
Authorization: Bearer <token>
```

### Query Parameters

- `limit` (int, optional, 1-100): Max users to return
- `after` (string, optional, Snowflake ID): Get users after this ID

### Response (200 OK)

```json
[
  {
    "user_id": "123456789012345678",
    "reacted_at": 1704067200
  },
  {
    "user_id": "234567890123456789",
    "reacted_at": 1704067300
  }
]
```

### Error Responses

- 400 Invalid message ID: ID format invalid
- 404 Message not found: Message doesn't exist

## Reaction Object

```json
{
  "emoji": "👍",
  "count": 5,
  "me": true
}
```

- `emoji` (string): Emoji identifier
- `count` (int): Number of users who reacted
- `me` (bool): Whether current user reacted

## Reaction User Object

```json
{
  "user_id": "123456789012345678",
  "reacted_at": 1704067200
}
```

- `user_id` (string): User's snowflake ID
- `reacted_at` (int): Unix timestamp of reaction

## Emoji Format

Reactions support:
- Unicode emoji: `👍`, `❤️`, `🎉`
- Custom emoji: `<a:name:id>` (e.g., `<:custom_emoji:123456789>`)

Note: Leading colons (`:name:`) are rejected. Use raw Unicode characters for standard emoji and the `<:name:id>` format for custom emoji.
