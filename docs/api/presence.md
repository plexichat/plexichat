# Presence API

Manage and read user presence. Use presence to show online status and custom status text.

**Base URL**

`https://api.plexichat.com/api/v1`

For development, use `http://localhost:8000/api/v1`.

All endpoints in this document are prefixed with `/api/v1` unless noted. All endpoints require `Authorization: Bearer <token>`.

## Update your presence

### `PUT /users/@me/presence`

Update the authenticated user's presence. This sets online status and, optionally, custom status text and emoji. The update is broadcast via `PRESENCE_UPDATE` to friends and members of shared servers, excluding users who block or are blocked by you.

**Request body**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `status` | string | Required | Online status. One of `online`, `idle`, `dnd`, `invisible`, `offline`. |
| `custom_status` | string | Optional | Custom status text. Up to 128 characters. Pass `""` to clear. Omit the field to keep the existing custom status. |
| `custom_emoji` | string | Optional | Custom status emoji. Omit to keep the existing emoji. |

Notes:

- An emoji-only status is valid (empty `custom_status` with non-empty `custom_emoji` is persisted). Only when both text and emoji are empty or omitted is the custom status cleared.
- A `status`-only update (common in the UI) does not clear an existing custom status; include `custom_status` or `custom_emoji` only when you intend to change it.
- `invisible` users appear as `offline` with no custom status or emoji to all viewers, including in the broadcast event. The server persists `invisible` but scrubs it on read and broadcast.

**Response `200 OK`**

Returns a `Presence` object.

```json
{
  "user_id": "123456789012345678",
  "status": "online",
  "custom_status": "Working on Plexichat",
  "custom_emoji": ":computer:",
  "last_seen": 1704067200
}
```

**Error responses**

| Code | Condition |
|------|-----------|
| 400 | Invalid `status` value |
| 401 | Invalid or expired token |
| 500 | Internal server error or presence module not available |

## Get a user's presence

### `GET /users/{user_id}/presence`

Get a user's presence. The server enforces a visibility gate: you can read presence only when you are the target user, a friend of the target, or share at least one server with the target. Otherwise the server returns `404`.

This is intentional. A `404` does not mean the user is offline; it means you do not have permission to view their presence.

**Path parameters**

| Name | Type | Description |
|------|------|-------------|
| `user_id` | integer | User Snowflake ID. |

**Response `200 OK`**

Returns a `Presence` object. If the user has never been online, `last_seen` is `null`. Invisible users are returned as `offline` with `custom_status` and `custom_emoji` set to `null`.

```json
{
  "user_id": "123456789012345678",
  "status": "online",
  "custom_status": "Working on Plexichat",
  "custom_emoji": ":computer:",
  "last_seen": 1704067200
}
```

**Error responses**

| Code | Condition |
|------|-----------|
| 401 | Invalid or expired token |
| 404 | User not found or you do not have permission to view their presence (not self, not a friend, and no shared server). Also returned when the target has blocked you or you have blocked the target. |
| 500 | Internal server error or presence module not available |

**Visibility rules**

The check is implemented in `_can_view_presence` (`plexichat/src/api/routes/presence.py:314`) and `_get_presence_targets` (`plexichat/src/api/routes/presence.py:89`):

- Allowed viewers: self, friends (`get_friend_ids`), or members of any shared server (`get_all_shared_member_ids`).
- Blocked users are excluded from presence targets and cannot view presence, even if they share a server.
- The underlying read uses `get_visible_presence(viewer_id, target_id)`, which already scrubs `invisible` to `offline`.

## Presence object

```json
{
  "user_id": "123456789012345678",
  "status": "online",
  "custom_status": "Working on Plexichat",
  "custom_emoji": ":computer:",
  "last_seen": 1704067200
}
```

| Field | Type | Description |
|-------|------|-------------|
| `user_id` | string (Snowflake ID) | User ID. |
| `status` | string | Current status: `online`, `idle`, `dnd`, `invisible`, or `offline`. Viewers see `invisible` as `offline`. |
| `custom_status` | string or null | Custom status text, or `null` if none. Invisible users return `null`. |
| `custom_emoji` | string or null | Custom status emoji, or `null` if none. Invisible users return `null`. |
| `last_seen` | integer or null | Last activity Unix timestamp. `null` when the user has never been seen (stored `0` is mapped to `null`). Invisible users may still expose a valid `last_seen` depending on server policy; do not use it to infer invisibility. |

## Related

- [WebSocket Events](../websocket/events.md) — `PRESENCE_UPDATE` event.

