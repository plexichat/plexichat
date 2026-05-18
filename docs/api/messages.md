# Messages API

Endpoints for message management.

**Base URL**: `https://api.plexichat.com/api/v1`

For development, use `http://localhost:8000/api/v1`.

All endpoints in this document are prefixed with `/api/v1/` unless otherwise specified.

## GET /channels/{channel_id}/messages

Get messages in a channel with pagination.

### Query Parameters

- `limit` (int, optional, 1-100): Max messages to return
- `before` (string, optional, Snowflake ID): Get messages before this ID
- `after` (string, optional, Snowflake ID): Get messages after this ID

### Example Request

```bash
curl -X GET "http://localhost:8000/api/v1/channels/123456789012345678/messages?limit=25&before=234567890123456789" \
  -H "Authorization: Bearer YOUR_SESSION_TOKEN"
```

### Response (200 OK)

```json
[
  {
    "id": "123456789012345678",
    "channel_id": "123456789012345678",
    "author_id": "123456789012345678",
    "author_username": "johndoe",
    "author_avatar_url": "http://localhost:8000/api/v1/avatars/users/123456789012345678",
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

- 403 Access denied: No permission to read messages
- 404 Channel not found: Channel doesn't exist

## GET /channels/{channel_id}/messages/search

Search messages in a channel by content.

### Headers

```
Authorization: Bearer <token>
```

### Query Parameters

- `content` (string, required): Search query
- `limit` (int, optional, 25): Max results (1-100)

### Example Request

```
GET /channels/123456789012345678/messages/search?content=hello&limit=10
```

### Response (200 OK)

Returns array of matching messages.

## POST /channels/{channel_id}/messages

Send a message to a channel.

### Request Body

- `content` (string, optional, Max 4000 chars): Message text
- `reply_to_id` (string, optional, Snowflake ID): Message to reply to
- `attachments` (array, optional): File attachments
- `embeds` (array, optional): Rich embeds

At least one of `content`, `attachments`, or `embeds` is required.

### Example Request

```bash
curl -X POST http://localhost:8000/api/v1/channels/123456789012345678/messages \
  -H "Authorization: Bearer YOUR_SESSION_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "content": "Hello, world!",
    "reply_to_id": "123456789012345678"
  }'
```

### Response (200 OK)

Returns the created message object.

### Error Responses

- 400 Empty message: No content, attachments, or embeds
- 400 Invalid content: Content validation failed
- 403 Permission denied: No send message permission
- 404 Channel not found: Channel doesn't exist

## GET /channels/{channel_id}/messages/{message_id}

Get a specific message.

### Headers

```
Authorization: Bearer <token>
```

### Response (200 OK)

Returns the message object.

### Error Responses

- 400 Invalid message ID: ID format invalid
- 403 Access denied: No permission to read
- 404 Message not found: Message doesn't exist

## PATCH /channels/{channel_id}/messages/{message_id}

Edit a message. Only the author can edit their messages.

### Request Body

- `content` (string, required, Max 4000 chars): New message content

### Example Request

```bash
curl -X PATCH http://localhost:8000/api/v1/channels/123456789012345678/messages/234567890123456789 \
  -H "Authorization: Bearer YOUR_SESSION_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "content": "Updated message content"
  }'
```

### Response (200 OK)

Returns the updated message object with `edited_at` timestamp set.

### Error Responses

- 400 Invalid content: Content validation failed
- 403 Not author: Only author can edit
- 404 Message not found: Message doesn't exist

## DELETE /channels/{channel_id}/messages/{message_id}

Delete a message. Author or users with manage messages permission can delete.

### Example Request

```bash
curl -X DELETE http://localhost:8000/api/v1/channels/123456789012345678/messages/234567890123456789 \
  -H "Authorization: Bearer YOUR_SESSION_TOKEN"
```

### Response (200 OK)

```json
{
  "success": true
}
```

### Error Responses

- 403 Permission denied: Not author and no manage permission
- 404 Message not found: Message doesn't exist

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

- 403 Permission denied: Missing manage messages permission
- 404 Message not found: Message doesn't exist

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

- `message_id` (string, optional): Mark as read up to this message ID

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
  "author_badges": ["staff"],
  "content": "Hello, world!",
  "created_at": 1704067200,
  "edited_at": null,
  "reply_to_id": null,
  "attachments": [],
  "embeds": [],
  "pinned": false,
  "status": "read",
  "delivery_count": 15,
  "read_count": 5,
  "read": true,
  "read_by": ["janedoe", "bobsmith"],
  "reactions": []
}
```

- `id` (string): Message's snowflake ID
- `channel_id` (string): Channel ID
- `author_id` (string): Author's user ID
- `author_username` (string): Author's username
- `author_avatar_url` (string?): Author's avatar URL
- `author_badges` (array): Author's profile badges
- `content` (string?): Message text content
- `created_at` (int): Unix timestamp of creation
- `edited_at` (int?): Unix timestamp of last edit
- `reply_to_id` (string?): ID of message being replied to
- `attachments` (array): File attachments
- `embeds` (array): Rich embeds
- `pinned` (bool): Whether message is pinned
- `status` (string?): User status: "sent", "delivered", or "read"
- `delivery_count` (int): Number of users who received the message
- `read_count` (int): Number of users who read the message
- `read` (bool): Whether current user has read it
- `read_by` (array): List of usernames who read it (sender only)
- `reactions` (array): Reaction data

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

- `id` (string): Attachment's snowflake ID
- `filename` (string): Original file name
- `content_type` (string): MIME type
- `size` (int): File size in bytes
- `url` (string): CDN URL to download
- `hash` (string?): File checksum

---

## Related Endpoints

- [Channels](channels.md) - Channel management
- [Reactions](reactions.md) - Message reactions
- [WebSocket Events](../websocket/events.md) - Real-time message events
