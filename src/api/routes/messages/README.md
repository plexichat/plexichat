# Messages routes

## Overview

This directory aggregates all message-related HTTP endpoints. You interact with server-channel and direct-message conversations through a single `router` that composes seven sub-routers.

The `__init__.py` file is the public entry point. It does not define handlers itself. It imports each sub-router and includes them on a shared `APIRouter(tags=["Messages"])`.

## Sub-packages and files

| Path | Purpose | Key routes |
|------|---------|------------|
| `messages_crud/` | Create, retrieve, edit, delete, and bulk-delete messages | `POST /channels/{channel_id}/messages`, `GET /channels/{channel_id}/messages/{message_id}`, `PATCH /channels/{channel_id}/messages/{message_id}`, `DELETE /channels/{channel_id}/messages/{message_id}`, `POST /channels/{channel_id}/messages/bulk-delete` |
| `messages_list.py` | List messages and pinned messages | `GET /channels/{channel_id}/messages`, `GET /channels/{channel_id}/pins` |
| `pins.py` | Pin and unpin messages | `PUT /channels/{channel_id}/pins/{message_id}`, `DELETE /channels/{channel_id}/pins/{message_id}` |
| `search.py` | Full-text search within a channel | `GET /channels/{channel_id}/messages/search?content=...` |
| `read_receipts.py` | Read receipts and unread counts | `GET /channels/{channel_id}/messages/unread`, `POST /channels/{channel_id}/messages/ack`, `POST /channels/ack/bulk`, `GET /users/@me/unread`, `GET /channels/{channel_id}/messages/last-read`, `POST /channels/{channel_id}/messages/mark-unread`, `POST /servers/{server_id}/messages/read-all` |
| `typing.py` | Typing indicators | `POST /channels/{channel_id}/typing` |
| `export.py` | Transcript export | `POST /channels/{channel_id}/messages/export`, `GET /channels/{channel_id}/messages/export/{export_id}`, `GET /channels/{channel_id}/messages/export/{export_id}/download` |
| `helpers.py` | Response mapping | `_message_to_response()` — converts internal message objects to `MessageResponse` |
| `messages.py` | Utility | `get_msg_id()` — extracts the message ID from dict or object forms |

## Message response shape

You receive `MessageResponse` for single-message and list endpoints. Notable fields include:

* `id`, `channel_id`, `author_id` — `SnowflakeID` strings.
* `read_by` — `List[ReaderInfo]` where each `ReaderInfo` contains `id`, `username`, and `avatar_url`. It is populated only for messages you authored and is omitted for other viewers.
* `author_username`, `author_avatar_url`, `author_badges` — resolved from `TokenInfo` with a fallback to `auth.get_user_profiles_bulk()`.
* `attachments` — `AttachmentResponse` items with signed URLs when `viewer_user_id` is present.
* `reactions`, `mentions`, `poll`, `poll_results`, `metadata` — enriched inline.

See `helpers.py:40` for the full mapping logic.

## Endpoints

### Core CRUD (via `messages_crud`)

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/channels/{channel_id}/messages` | Send a message. Body must contain `content`, `attachments`, `embeds`, or `poll`. |
| `GET` | `/channels/{channel_id}/messages/{message_id}` | Get a single message. Returns `404` if the message is not in the addressed channel. |
| `PATCH` | `/channels/{channel_id}/messages/{message_id}` | Edit a message. Supports optimistic concurrency via `expected_edited_at` (`409` on conflict). |
| `DELETE` | `/channels/{channel_id}/messages/{message_id}` | Delete a message. Broadcasts both a tombstone `MESSAGE_UPDATE` and `MESSAGE_DELETE`. |
| `POST` | `/channels/{channel_id}/messages/bulk-delete` | Bulk-delete up to 100 messages. Request body is `BulkDeleteRequest` (`message_ids: List[SnowflakeID]`). Only eligible messages are removed; the response indicates partial success. |

### Listing and pinning

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/channels/{channel_id}/messages?limit=&before=&after=` | Get channel messages. `limit` is `1–100` (default `50`). Supports cursor pagination. |
| `GET` | `/channels/{channel_id}/pins` | Get pinned messages. |
| `PUT` | `/channels/{channel_id}/pins/{message_id}` | Pin a message. |
| `DELETE` | `/channels/{channel_id}/pins/{message_id}` | Unpin a message. |

### Read receipts

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/channels/{channel_id}/messages/unread` | Get the unread count for a channel. |
| `POST` | `/channels/{channel_id}/messages/ack?message_id=` | Mark messages as read up to `message_id` (or all if omitted). |
| `POST` | `/channels/ack/bulk` | Bulk-acknowledge multiple channels. Body is `Dict[channel_id, message_id]`. |
| `GET` | `/users/@me/unread` | Get unread counts for all conversations. |
| `GET` | `/channels/{channel_id}/messages/last-read` | Get the last read position. |
| `POST` | `/channels/{channel_id}/messages/mark-unread` | Mark a channel as unread from `message_id` onward. |
| `POST` | `/servers/{server_id}/messages/read-all` | Mark all channels in a server as read. |

### Utilities

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/channels/{channel_id}/messages/search?content=&limit=` | Search messages by content. `limit` is `1–100` (default `25`). |
| `POST` | `/channels/{channel_id}/typing` | Trigger a typing indicator. Silently succeeds for `voice` and `stage` channels. |
| `POST` | `/channels/{channel_id}/messages/export` | Request a transcript export (`json`, `csv`, `txt`, `html`). |
| `GET` | `/channels/{channel_id}/messages/export/{export_id}` | Get export status. Ownership is enforced; other users receive `404`. |
| `GET` | `/channels/{channel_id}/messages/export/{export_id}/download` | Download a completed export before `expires_at`. |

## How the router resolves channels

You pass a `channel_id` path parameter for every endpoint. The handlers:

1. Try to resolve `channel_id` as a server channel via `servers.get_channel()`. If found, they use the linked `conversation_id`.
2. Fall back to treating `channel_id` as a direct `conversation_id` for DM or group conversations.
3. Return `404 Channel not found` when neither resolution succeeds.

This fallback is implemented in `messages_list.py:93`, `read_receipts.py:68`, and `messages_crud/base.py:130`.

## Error handling

All routes use a consistent shape: `{"error": {"code": <int>, "message": <string>}}`.

| HTTP code | When you see it |
|-----------|-----------------|
| `400` | Invalid `channel_id`, `message_id`, or empty body; `BulkDeleteRequest` exceeds 100 IDs; invalid export format |
| `401` | Missing or invalid token |
| `403` | `ChannelAccessDeniedError` or `PermissionDeniedError`; DM spam protection; timed-out members |
| `404` | `ChannelNotFoundError`, `ConversationNotFoundError`, or `MessageNotFoundError`; channel/message mismatch (probes return `404` without revealing existence) |
| `409` | Edit conflict (`expected_edited_at` is stale) or `EDIT_WINDOW_EXPIRED` |
| `500` | Missing module or unexpected internal error |

## Dependencies

* `src.api.get_servers()` — channel and server lookups, permission checks.
* `src.api.get_messaging()` — message persistence, read receipts, export.
* `src.api.get_auth()` — bulk profile resolution for `author_*` and `read_by`.
* `src.api.get_media()` — signed attachment URLs.
* `src.api.get_reactions()` and `src.api.get_polls()` — enrichment in `helpers.py`.
* `src.api.websocket` — fire-and-forget `MESSAGE_CREATE`, `MESSAGE_UPDATE`, `MESSAGE_DELETE`, and `MESSAGE_DELETE_BULK` broadcasts.

## Examples

Send a message:

```python
POST /channels/123/messages
{
  "content": "hello world",
  "reply_to_id": null,
  "attachments": [],
  "embeds": [],
  "poll": null
}
```

Bulk-delete messages:

```python
POST /channels/123/messages/bulk-delete
{
  "message_ids": ["111", "222", "333"]
}
# Success: {"success": true}
# Partial: {"success": false, "message": "Deleted 2 of 3 messages; the rest were not eligible"}
# Limit: 400 if len(message_ids) > 100
```

Get channel messages with pagination:

```python
GET /channels/123/messages?limit=50&before=999&after=100
```

## Notes

* Cached endpoints use `prefix="messages_api"` (`ttl=10`) and `prefix="channel_pins_api"` (`ttl=120`). See `messages_list.py:39`.
* `read_by` uses `ReaderInfo` objects (`id`, `username`, `avatar_url`), not bare user IDs. See `src/api/schemas/messages.py:84`.
* `PATCH /channels/{channel_id}/messages/{message_id}` takes `message_id: SnowflakeIDParam`, matching the sibling GET/DELETE routes; malformed IDs fail validation with `422`.
* Broadcasts filter recipients by `channels.view` permission so private-channel content does not leak. See `messages_crud/broadcast.py:26`.
