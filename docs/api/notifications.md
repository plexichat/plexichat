# Notifications API

Retrieve and manage notifications for the authenticated user. Use these endpoints to power unread badges, inbox lists, and notification clearing.

**Base URL**

`https://api.plexichat.com/api/v1`

For development, use `http://localhost:8000/api/v1`.

All endpoints require `Authorization: Bearer <token>`.

## List notifications

### `GET /users/@me/notifications`

Get your notification feed.

**Query parameters**

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `limit` | integer | Optional | Max notifications to return. Default `20`. |
| `unread_only` | boolean | Optional | When `true`, returns only unread notifications. Default `false`. |

**Response `200 OK`**

```json
{
  "notifications": [
    {
      "id": "123456789012345678",
      "type": "mention",
      "title": "You were mentioned",
      "content": "Hello @you in #general",
      "content_preview": "Hello @you in #general",
      "read": false,
      "created_at": 1704067200,
      "link": "/channels/111/222/333",
      "sender_id": "123456789012345678"
    }
  ],
  "unread_count": 3,
  "total_unread": 12
}
```

| Field | Type | Description |
|-------|------|-------------|
| `notifications` | array of `NotificationInfo` | Notifications for the requested page. |
| `unread_count` | integer | Historical mention count (kept for existing clients). |
| `total_unread` | integer | Exact count of all unread notification rows, independent of `limit`. Prefer this for badge counts. |

`NotificationInfo` fields:

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Snowflake ID. |
| `type` | string | Notification type. Currently `mention`. |
| `title` | string | Human-readable title derived from `mention_type` (`user`, `role`, `everyone`, `here`). |
| `content` | string | Notification content. |
| `content_preview` | string or null | Short preview. |
| `read` | boolean | Whether the notification has been read. |
| `created_at` | integer | Creation Unix timestamp. |
| `link` | string or null | Deep link. Format `/channels/{server_id}/{channel_id}/{message_id}` for servers or `/channels/@me/{conversation_id}/{message_id}` for DMs. |
| `sender_id` | string or null | Author user ID that triggered the notification. |

**Error responses**

| Code | Condition |
|------|-----------|
| 401 | Invalid or expired token |
| 500 | Internal server error |

## Mark all notifications as read

### `PUT /users/@me/notifications/read-all`

Mark all of your notifications as read. Use this for an explicit inbox clear action.

**Response `200 OK`**

```json
{ "success": true }
```

**Error responses**

| Code | Condition |
|------|-----------|
| 401 | Invalid or expired token |
| 500 | Internal server error |

## Mark a notification as read

### `PUT /users/@me/notifications/{notification_id}/read`

Mark a single notification as read. Call this when the user visits the target content.

**Path parameters**

| Name | Type | Description |
|------|------|-------------|
| `notification_id` | string (Snowflake ID) | Notification to mark as read. |

**Response `200 OK`**

```json
{ "success": true }
```

**Error responses**

| Code | Condition |
|------|-----------|
| 400 | Invalid notification ID format |
| 401 | Invalid or expired token |
| 404 | Notification not found |
| 500 | Internal server error |

## Client guidance

- Render unread badges from `total_unread`, not from `notifications.length` or `unread_count`.
- Prefer targeted `PUT .../read` calls over repeatedly re-fetching the feed with large `limit` values.
- Keep local counts synchronized with server responses after `read-all` or single `read` calls.
- Pair this REST feed with gateway notification events when available to avoid polling.

## Related endpoints

- [Read receipts](messages.md#read-receipts)
- [WebSocket Events](../websocket/events.md)
