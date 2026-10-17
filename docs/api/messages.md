# Messages API

Manage messages, pins, read receipts, and typing indicators.

**Base URL**

`https://api.plexichat.com/api/v1`

For development, use `http://localhost:8000/api/v1`.

All endpoints in this document are prefixed with `/api/v1` unless noted. All endpoints require `Authorization: Bearer <token>`.

## List messages

### `GET /channels/{channel_id}/messages`

Get messages in a channel with pagination. Works for server channels and DM conversations.

**Path parameters**

| Name | Type | Description |
|------|------|-------------|
| `channel_id` | string (Snowflake ID) | Channel ID. |

**Query parameters**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `limit` | integer | Optional | Max messages to return. 1-100. Default 25. |
| `before` | string (Snowflake ID) | Optional | Get messages before this ID. |
| `after` | string (Snowflake ID) | Optional | Get messages after this ID. |

**Response `200 OK`**

Returns an array of `Message` objects.

**Error responses**

| Code | Condition |
|------|-----------|
| 403 | No permission to read messages |
| 404 | Channel not found |

**Example**

```bash
curl -X GET "http://localhost:8000/api/v1/channels/123456789012345678/messages?limit=25&before=234567890123456789" \
  -H "Authorization: Bearer YOUR_SESSION_TOKEN"
```

## Search messages

### `GET /channels/{channel_id}/messages/search`

Search messages in a channel by content.

**Query parameters**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `content` | string | Required | Search query. |
| `limit` | integer | Optional | Max results. 1-100. Default 25. |

**Response `200 OK`**

Returns an array of matching `Message` objects.

## Send a message

### `POST /channels/{channel_id}/messages`

Send a message to a channel. Works for both server channels and DM conversations. The server runs auto-moderation fast checks before creation; matching keyword or spam rules return `400` with `code: MESSAGE_BLOCKED`.

**Path parameters**

| Name | Type | Description |
|------|------|-------------|
| `channel_id` | string (Snowflake ID) | Channel ID. |

**Request body**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `content` | string | Optional | Message text. Up to 4000 characters. |
| `reply_to_id` | string (Snowflake ID) | Optional | Message to reply to. |
| `attachments` | array of `AttachmentRequest` | Optional | File attachments. |
| `embeds` | array of objects | Optional | Rich embeds. |
| `poll` | object (`PollInlineCreateRequest`) | Optional | Poll to attach. See Polls API. |

At least one of `content`, `attachments`, `embeds`, or `poll` is required. Empty `content` with `attachments`, `embeds`, or `poll` is substituted with a zero-width space to pass validation without visible text.

`AttachmentRequest` fields: `filename`, `content_type`, `size`, `url`, `hash` (optional), `metadata` (optional).

**Response `200 OK`**

Returns the created `Message` object.

**Error responses**

| Code | Condition |
|------|-----------|
| 400 | Empty message or content validation failed or `MESSAGE_BLOCKED` by auto-moderation |
| 403 | No permission to send |
| 404 | Channel not found |

## Get a message

### `GET /channels/{channel_id}/messages/{message_id}`

Get a specific message. The URL `channel_id` must match the message's actual channel; a mismatch returns `404`.

**Error responses**

| Code | Condition |
|------|-----------|
| 400 | Invalid message ID |
| 403 | No permission to read |
| 404 | Message not found |

## Edit a message

### `PATCH /channels/{channel_id}/messages/{message_id}`

Edit a message. Only the author can edit. Edits are subject to `messaging.edit_window_seconds`: when greater than 0, edits older than that window return `400`; `0` means no limit.

**Request body**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `content` | string | Required | New message content. Up to 4000 characters. |
| `expected_edited_at` | integer | Optional | Optimistic concurrency token. Pass the last-seen `edited_at` or `created_at`; stale values return `409`. Omit for last-write-wins. |

**Response `200 OK`**

Returns the updated `Message` object with `edited_at` set.

**Error responses**

| Code | Condition |
|------|-----------|
| 400 | Invalid content or edit window exceeded |
| 403 | Not author |
| 404 | Message not found |
| 409 | Edit conflict (stale `expected_edited_at`) |

## Delete a message

### `DELETE /channels/{channel_id}/messages/{message_id}`

Delete a message. The author, a conversation owner or admin, or a server moderator with `messages.manage` can delete. The URL `channel_id` must match the message's actual channel; a mismatch returns the same `404` as a nonexistent message.

On deletion the server broadcasts both `MESSAGE_DELETE` and a `MESSAGE_UPDATE` tombstone (`deleted: true`, `content: ""`) so list refetches and live clients stay consistent. If the message metadata contains `poll_id`, the poll is also deleted.

**Response `200 OK`**

```json
{ "success": true }
```

**Error responses**

| Code | Condition |
|------|-----------|
| 400 | Invalid message ID |
| 403 | Not author and no manage permission |
| 404 | Message not found |

## Bulk-delete messages

### `POST /channels/{channel_id}/messages/bulk-delete`

Delete up to 100 messages at once. Only messages that belong to the specified channel and that the caller is authorized to delete are removed. The server emits `MESSAGE_DELETE_BULK` for connected clients. Requires `messages.manage` permission for non-author messages; authors can only bulk-delete their own messages.

**Path parameters**

| Name | Type | Description |
|------|------|-------------|
| `channel_id` | string (Snowflake ID) | Channel ID. Must match each message's channel. |

**Request body**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `message_ids` | array of strings (Snowflake IDs) | Required | Message IDs to delete. Max 100. Empty array returns `success: true`. |

**Response `200 OK`**

Returns `SuccessResponse`:

```json
{ "success": true }
```

If some IDs were not eligible (wrong channel, no permission, or not found), the response is still `200` but includes a partial result:

| `success` | `message` |
|-----------|-----------|
| `true` | All requested messages deleted (no message field) |
| `false` | `No eligible messages were deleted` (none deleted) |
| `false` | `Deleted N of M messages; the rest were not eligible` (partial) |

**Error responses**

| Code | Condition |
|------|-----------|
| 400 | Invalid channel ID, invalid message ID, or more than 100 IDs |
| 403 | Missing `messages.manage` permission or access denied |
| 404 | Channel or message lookup failed for some implementations (service may also return partial `200` instead) |

**Example**

```bash
curl -X POST http://localhost:8000/api/v1/channels/123456789012345678/messages/bulk-delete \
  -H "Authorization: Bearer YOUR_SESSION_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{ "message_ids": ["234567890123456789", "345678901234567890"] }'
```

---

## Pinned messages

### `GET /channels/{channel_id}/pins`

Get all pinned messages in a channel.

**Response `200 OK`**

Returns an array of `Message` objects where `pinned` is `true`.

### `PUT /channels/{channel_id}/pins/{message_id}`

Pin a message. Requires `messages.manage`.

**Response `200 OK`**

```json
{ "success": true }
```

**Error responses**

| Code | Condition |
|------|-----------|
| 403 | Missing `messages.manage` |
| 404 | Message not found |

### `DELETE /channels/{channel_id}/pins/{message_id}`

Unpin a message. Requires `messages.manage`.

**Response `200 OK`**

```json
{ "success": true }
```

---

## Read receipts

### `POST /channels/{channel_id}/messages/ack`

Mark messages as read. If `message_id` is provided, marks all messages up to and including that ID as read; otherwise marks all messages in the channel as read. Voice and stage channels return `messages_marked: 0`. The server also marks notifications for the channel as read and broadcasts `MESSAGE_ACK`.

**Query parameters**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `message_id` | string (Snowflake ID) | Optional | Mark as read up to this ID. |

**Response `200 OK`**

```json
{ "success": true, "messages_marked": 15 }
```

### `GET /channels/{channel_id}/messages/unread`

Get unread count for a channel. Resolves server channels to their underlying conversation.

**Response `200 OK`**

```json
{ "channel_id": "123456789012345678", "unread_count": 5 }
```

### `GET /channels/{channel_id}/messages/last-read`

Get your last read position in a channel.

**Response `200 OK`**

```json
{
  "channel_id": "123456789012345678",
  "last_read_message_id": "234567890123456789",
  "last_read_at": 1704067200
}
```

| Field | Type | Description |
|-------|------|-------------|
| `channel_id` | string (Snowflake ID) | Channel ID. |
| `last_read_message_id` | string or null | Last read message ID. |
| `last_read_at` | integer or null | Timestamp of last read. |

### `POST /channels/{channel_id}/messages/mark-unread`

Mark messages as unread.

**Request body**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `message_id` | string (Snowflake ID) | Optional | Mark unread from this message onwards. |

**Response `200 OK`**

```json
{ "success": true, "unread_count": 3 }
```

### `GET /users/@me/unread`

Get unread counts for all conversations. Result is cached for 10 seconds.

**Response `200 OK`**

```json
{ "unread_counts": { "123456789012345678": 5, "234567890123456789": 12 } }
```

### `POST /channels/ack/bulk`

Mark messages as read in multiple channels.

**Request body**

A JSON object mapping `channel_id` to `message_id` or `null`. Example: `{ "123": "456", "789": null }`.

**Response `200 OK`**

```json
{ "success": true }
```

### `POST /servers/{server_id}/messages/read-all`

Mark all channels in a server as read.

**Response `200 OK`**

```json
{ "success": true, "channels_marked": 8 }
```

---

## Typing indicator

### `POST /channels/{channel_id}/typing`

Trigger a typing indicator. Broadcasts to other participants. Works for server channels and DM conversations.

**Response `200 OK`**

```json
{ "success": true }
```

---

## Message object

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
  "deleted": false,
  "reply_to_id": null,
  "attachments": [],
  "embeds": [],
  "pinned": false,
  "status": "read",
  "delivery_count": 15,
  "read_count": 5,
  "read": true,
  "read_by": [
    {"id": "123456789012345678", "username": "janedoe", "avatar_url": "https://cdn.example.com/avatars/456.png"}
  ],
  "mentions": [],
  "reactions": [],
  "metadata": null,
  "poll": null,
  "poll_results": null
}
```

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Snowflake ID. |
| `channel_id` | string | Channel ID. |
| `author_id` | string | Author user ID. |
| `author_username` | string or null | Author username. |
| `author_avatar_url` | string or null | Author avatar URL. |
| `author_badges` | array of strings | Author badges. |
| `content` | string or null | Message text. Empty or `""` for tombstones. |
| `created_at` | integer | Creation Unix timestamp. |
| `edited_at` | integer or null | Last edit timestamp. |
| `deleted` | boolean | When `true`, render a tombstone. |
| `reply_to_id` | string or null | Reply target ID. |
| `attachments` | array of `AttachmentResponse` | File attachments. |
| `embeds` | array | Rich embeds. |
| `pinned` | boolean | Pinned status. |
| `status` | string or null | `sent`, `delivered`, or `read` for current user. |
| `delivery_count` | integer | Number of users who received the message. |
| `read_count` | integer | Number of users who read the message. |
| `read` | boolean | Whether the current user has read it. |
| `read_by` | array of `ReaderInfo` | Users who read the message. Each entry has `id`, `username`, `avatar_url`. Only visible to the sender. |
| `mentions` | array of strings | User IDs mentioned. |
| `reactions` | array | Reaction data. |
| `metadata` | object or null | Additional metadata (for example, `poll_id`). |
| `poll` | `PollResponse` or null | Attached poll. |
| `poll_results` | `PollResultsResponse` or null | Current results and your vote. |

## Attachment object

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
| `id` | string | Attachment Snowflake ID. |
| `filename` | string | Original filename. |
| `content_type` | string | MIME type. |
| `size` | integer | File size in bytes. |
| `url` | string | Download URL. |
| `hash` | string or null | SHA-256 hash. |

## Related endpoints

- [Channels](channels.md)
- [Reactions](reactions.md)
- [Polls](polls.md)
- [WebSocket Events](../websocket/events.md)
