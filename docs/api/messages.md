# Messages API

Endpoints for message management.

## GET /channels/{channel_id}/messages

Get messages in a channel with pagination.

### Headers

```
Authorization: Bearer <token>
```

### Query Parameters

| Parameter | Type | Default | Constraints | Description |
|-----------|------|---------|-------------|-------------|
| limit | int | 50 | 1-100 | Max messages to return |
| before | string | - | Snowflake ID | Get messages before this ID |
| after | string | - | Snowflake ID | Get messages after this ID |

### Example Request

```
GET /channels/123456789012345678/messages?limit=25&before=234567890123456789
```

### Response (200 OK)

```json
[
  {
    "id": "123456789012345678",
    "channel_id": "123456789012345678",
    "author_id": "123456789012345678",
    "author_username": "johndoe",
    "author_avatar_url": "https://cdn.example.com/avatars/123.png",
    "content": "Hello, world!",
    "created_at": 1704067200,
    "edited_at": null,
    "reply_to_id": null,
    "attachments": [],
    "embeds": [],
    "pinned": false,
    "reactions": []
  }
]
```

### Error Responses

| Status | Code | Description |
|--------|------|-------------|
| 403 | Access denied | No permission to read messages |
| 404 | Channel not found | Channel doesn't exist |

## GET /channels/{channel_id}/messages/search

Search messages in a channel by content.

### Headers

```
Authorization: Bearer <token>
```

### Query Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| content | string | Yes | - | Search query |
| limit | int | No | 25 | Max results (1-100) |

### Example Request

```
GET /channels/123456789012345678/messages/search?content=hello&limit=10
```

### Response (200 OK)

Returns array of matching messages.

## POST /channels/{channel_id}/messages

Send a message to a channel.

### Headers

```
Authorization: Bearer <token>
```

### Request Body

| Field | Type | Required | Constraints | Description |
|-------|------|----------|-------------|-------------|
| content | string | Conditional | Max 4000 chars | Message text |
| reply_to_id | string | No | Snowflake ID | Message to reply to |
| attachments | array | No | - | File attachments |
| embeds | array | No | - | Rich embeds |

At least one of `content`, `attachments`, or `embeds` is required.

### Attachment Object

| Field | Type | Description |
|-------|------|-------------|
| filename | string | File name |
| content_type | string | MIME type |
| size | int | File size in bytes |
| url | string | File URL |

### Example Request

```json
{
  "content": "Hello, world!",
  "reply_to_id": "123456789012345678"
}
```

### Response (200 OK)

Returns the created message object.

### Error Responses

| Status | Code | Description |
|--------|------|-------------|
| 400 | Empty message | No content, attachments, or embeds |
| 400 | Invalid content | Content validation failed |
| 403 | Permission denied | No send message permission |
| 404 | Channel not found | Channel doesn't exist |

## GET /channels/{channel_id}/messages/{message_id}

Get a specific message.

### Headers

```
Authorization: Bearer <token>
```

### Response (200 OK)

Returns the message object.

### Error Responses

| Status | Code | Description |
|--------|------|-------------|
| 400 | Invalid message ID | ID format invalid |
| 403 | Access denied | No permission to read |
| 404 | Message not found | Message doesn't exist |

## PATCH /channels/{channel_id}/messages/{message_id}

Edit a message. Only the author can edit their messages.

### Headers

```
Authorization: Bearer <token>
```

### Request Body

| Field | Type | Required | Constraints | Description |
|-------|------|----------|-------------|-------------|
| content | string | Yes | Max 4000 chars | New message content |

### Example Request

```json
{
  "content": "Updated message content"
}
```

### Response (200 OK)

Returns the updated message object with `edited_at` timestamp set.

### Error Responses

| Status | Code | Description |
|--------|------|-------------|
| 400 | Invalid content | Content validation failed |
| 403 | Not author | Only author can edit |
| 404 | Message not found | Message doesn't exist |

## DELETE /channels/{channel_id}/messages/{message_id}

Delete a message. Author or users with manage messages permission can delete.

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
| 403 | Permission denied | Not author and no manage permission |
| 404 | Message not found | Message doesn't exist |

---

## Pinned Messages

## GET /channels/{channel_id}/pins

Get all pinned messages in a channel.

### Headers

```
Authorization: Bearer <token>
```

### Response (200 OK)

Returns array of pinned message objects.

## PUT /channels/{channel_id}/pins/{message_id}

Pin a message in a channel. Requires manage messages permission.

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
| 403 | Permission denied | Missing manage messages permission |
| 404 | Message not found | Message doesn't exist |

## DELETE /channels/{channel_id}/pins/{message_id}

Unpin a message from a channel. Requires manage messages permission.

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

## Read Receipts

## POST /channels/{channel_id}/messages/ack

Mark messages as read in a channel.

### Headers

```
Authorization: Bearer <token>
```

### Query Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| message_id | string | No | Mark as read up to this message ID |

If `message_id` is provided, marks all messages up to and including that message as read.
If not provided, marks all messages in the channel as read.

### Response (200 OK)

```json
{
  "success": true,
  "messages_marked": 15
}
```

## GET /channels/{channel_id}/messages/unread

Get unread message count for a channel.

### Headers

```
Authorization: Bearer <token>
```

### Response (200 OK)

```json
{
  "channel_id": "123456789012345678",
  "unread_count": 5
}
```

## GET /users/@me/unread

Get unread message counts for all conversations.

### Headers

```
Authorization: Bearer <token>
```

### Response (200 OK)

```json
{
  "unread_counts": {
    "123456789012345678": 5,
    "234567890123456789": 12
  }
}
```

---

## Typing Indicator

## POST /channels/{channel_id}/typing

Trigger typing indicator in a channel.

Broadcasts a typing event to other users in the channel. Works for both server channels and DM conversations.

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

## Message Object

```json
{
  "id": "123456789012345678",
  "channel_id": "123456789012345678",
  "author_id": "123456789012345678",
  "author_username": "johndoe",
  "author_avatar_url": "https://cdn.example.com/avatars/123.png",
  "content": "Hello, world!",
  "created_at": 1704067200,
  "edited_at": null,
  "reply_to_id": null,
  "attachments": [],
  "embeds": [],
  "pinned": false,
  "reactions": []
}
```

| Field | Type | Description |
|-------|------|-------------|
| id | string | Message's snowflake ID |
| channel_id | string | Channel ID |
| author_id | string | Author's user ID |
| author_username | string | Author's username |
| author_avatar_url | string? | Author's avatar URL |
| content | string? | Message text content |
| created_at | int | Unix timestamp of creation |
| edited_at | int? | Unix timestamp of last edit |
| reply_to_id | string? | ID of message being replied to |
| attachments | array | File attachments |
| embeds | array | Rich embeds |
| pinned | bool | Whether message is pinned |
| reactions | array | Reaction data |

## Attachment Object

```json
{
  "id": "123456789012345678",
  "filename": "image.png",
  "content_type": "image/png",
  "size": 12345,
  "url": "https://cdn.example.com/attachments/image.png",
  "hash": "abc123..."
}
```

| Field | Type | Description |
|-------|------|-------------|
| id | string | Attachment's snowflake ID |
| filename | string | Original file name |
| content_type | string | MIME type |
| size | int | File size in bytes |
| url | string | CDN URL to download |
| hash | string? | File checksum |

---

## Related Endpoints

- [Channels](channels.md) - Channel management
- [Reactions](reactions.md) - Message reactions
- [WebSocket Events](../websocket/events.md) - Real-time message events
