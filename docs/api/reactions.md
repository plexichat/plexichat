# Reactions API

Manage emoji reactions on messages. You use this API to add, remove, list, and moderate reactions.

All endpoints in this document are relative to `https://api.plexichat.com`.
For local development, use `http://localhost:8000`.

You must authenticate with `Authorization: Bearer <token>` for all reaction endpoints.

Endpoints that include a `channel_id` validate that the message actually belongs to that channel. If the message does not exist in the given channel, the API returns `404 Message not found`. This prevents channel ID spoofing.

Rate limits apply per user:

| Route | Limit |
| --- | --- |
| `PUT /reactions` | 10 requests per second |
| `DELETE /reactions` | 15 requests per second |
| `DELETE /reactions/emoji/all` | Rate-limited |
| `DELETE /reactions/all` | Rate-limited |

## Reaction resource

A reaction aggregates users who reacted with the same emoji.

```json
{
  "emoji": "👍",
  "count": 5,
  "me": true,
  "url": null,
  "is_custom": false,
  "custom_emoji_id": null
}
```

### Properties

| Field | Type | Description |
| --- | --- | --- |
| `emoji` | string | Emoji identifier. Unicode or custom emoji. |
| `count` | integer | Number of users who reacted with this emoji. |
| `me` | boolean | Whether the current user reacted. |
| `url` | string or null | URL for a custom emoji. |
| `is_custom` | boolean | Whether the emoji is a custom server emoji. |
| `custom_emoji_id` | string or null | Snowflake ID of the custom emoji, if `is_custom` is `true`. |

```json
{
  "user_id": "123456789012345678",
  "reacted_at": 1704067200
}
```

| Field | Type | Description |
| --- | --- | --- |
| `user_id` | string | Snowflake ID of the user who reacted. |
| `reacted_at` | integer | Unix timestamp in milliseconds when the reaction was added. |

### Emoji format

| Format | Example | Notes |
| --- | --- | --- |
| Unicode | `👍`, `❤️`, `🎉` | Send the raw Unicode character, URL-encoded when in the path. |
| Custom | `<:custom_emoji:123456789>` or `<a:name:id>` | Animated custom emoji includes the `a` prefix. |

Do not use short-code format (`:name:`). The API rejects it with `400 Invalid emoji`.

## Add a reaction

Adds the specified emoji to a message.

```
PUT /api/v1/channels/{channel_id}/messages/{message_id}/reactions/{emoji}
```

You must have permission to add reactions in the channel. If you are timed out in the server, the API returns `403`.

You can repeat this request safely. If the reaction already exists, the API returns `200` and does not create a duplicate.

### Path parameters

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| `channel_id` | string | Yes | Snowflake ID of the channel. |
| `message_id` | string | Yes | Snowflake ID of the message. |
| `emoji` | string | Yes | Emoji, URL-encoded. The server decodes it before storage. |

### Example request

```bash
curl -X PUT https://api.plexichat.com/api/v1/channels/123456789012345678/messages/234567890123456789/reactions/%F0%9F%91%8D \
  -H "Authorization: Bearer YOUR_SESSION_TOKEN"
```

### Response

`200 OK`

```json
{
  "success": true
}
```

### Errors

| Status | Description |
| --- | --- |
| `400` | Invalid message ID, channel ID, or emoji. Reaction limit exceeded. |
| `401` | Not authenticated. |
| `403` | Missing permission, blocked, or timed out in the server. |
| `404` | Message does not exist or does not belong to the channel. |
| `503` | Reactions module not available. |

## Remove your reaction

Removes your own reaction from a message. This operation is idempotent. If the reaction does not exist, you still receive `200`.

```
DELETE /api/v1/channels/{channel_id}/messages/{message_id}/reactions/{emoji}
```

### Path parameters

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| `channel_id` | string | Yes | Snowflake ID of the channel. |
| `message_id` | string | Yes | Snowflake ID of the message. |
| `emoji` | string | Yes | Emoji, URL-encoded. |

### Example request

```bash
curl -X DELETE https://api.plexichat.com/api/v1/channels/123456789012345678/messages/234567890123456789/reactions/%F0%9F%91%8D \
  -H "Authorization: Bearer YOUR_SESSION_TOKEN"
```

### Response

`200 OK`

```json
{
  "success": true
}
```

An invalid or unrecognized emoji returns `400 Invalid emoji` on this route, matching the `PUT` validation.

### Errors

| Status | Description |
| --- | --- |
| `400` | Invalid message ID, channel ID, or emoji. |
| `401` | Not authenticated. |
| `404` | Message does not exist or does not belong to the channel. |

## Remove all reactions of one emoji

Removes every reaction of a specific emoji from a message. This is a moderation action that requires the `manage messages` permission. The server emits a `MESSAGE_REACTION_REMOVE_EMOJI` event so clients can update reaction counts live.

```
DELETE /api/v1/channels/{channel_id}/messages/{message_id}/reactions/{emoji}/all
```

Important: This route must be registered before the single-user `DELETE .../reactions/{emoji}` route. The `{emoji}` parameter uses a greedy `path` converter, so registering it after would cause `.../reactions/<emoji>/all` to be captured as an emoji named `<emoji>/all`.

### Path parameters

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| `channel_id` | string | Yes | Snowflake ID of the channel. |
| `message_id` | string | Yes | Snowflake ID of the message. |
| `emoji` | string | Yes | Emoji to clear, URL-encoded. |

### Example request

```bash
curl -X DELETE https://api.plexichat.com/api/v1/channels/123456789012345678/messages/234567890123456789/reactions/%F0%9F%91%8D/all \
  -H "Authorization: Bearer YOUR_SESSION_TOKEN"
```

### Response

`200 OK`

```json
{
  "success": true,
  "message": "Removed 12 reactions"
}
```

`message` indicates how many reactions were removed.

### Errors

| Status | Description |
| --- | --- |
| `400` | Invalid message, channel, or emoji. |
| `401` | Not authenticated. |
| `403` | You do not have permission to moderate reactions. |
| `404` | Message does not exist or does not belong to the channel. |
| `503` | Reactions module not available. |

## Clear all reactions

Removes all reactions from a message. Requires the `manage messages` permission. The server emits a `MESSAGE_REACTION_REMOVE_ALL` event.

```
DELETE /api/v1/channels/{channel_id}/messages/{message_id}/reactions
```

### Path parameters

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| `channel_id` | string | Yes | Snowflake ID of the channel. |
| `message_id` | string | Yes | Snowflake ID of the message. |

### Example request

```bash
curl -X DELETE https://api.plexichat.com/api/v1/channels/123456789012345678/messages/234567890123456789/reactions \
  -H "Authorization: Bearer YOUR_SESSION_TOKEN"
```

### Response

`200 OK`

```json
{
  "success": true
}
```

### Errors

| Status | Description |
| --- | --- |
| `400` | Invalid message or channel ID. |
| `401` | Not authenticated. |
| `403` | You do not have permission to clear reactions. |
| `404` | Message does not exist or does not belong to the channel. |
| `503` | Reactions module not available. |

## List reactions on a message

Returns aggregated reactions for a message.

```
GET /api/v1/channels/{channel_id}/messages/{message_id}/reactions
```

### Path parameters

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| `channel_id` | string | Yes | Snowflake ID of the channel. |
| `message_id` | string | Yes | Snowflake ID of the message. |

### Response

`200 OK`

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

### Errors

| Status | Description |
| --- | --- |
| `400` | Invalid message ID format. |
| `401` | Not authenticated. |
| `404` | Message does not exist. |

## List users who reacted with an emoji

Returns users who added a specific emoji to a message. Results are paginated.

```
GET /api/v1/channels/{channel_id}/messages/{message_id}/reactions/{emoji}
```

### Path parameters

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| `channel_id` | string | Yes | Snowflake ID of the channel. |
| `message_id` | string | Yes | Snowflake ID of the message. |
| `emoji` | string | Yes | Emoji, URL-encoded. |

### Query parameters

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| `limit` | integer | No | Maximum users to return. `1`–`100`, default `50`. |
| `after` | string | No | Snowflake ID. Returns users after this user. |

### Example request

```bash
curl -X GET "https://api.plexichat.com/api/v1/channels/123456789012345678/messages/234567890123456789/reactions/%F0%9F%91%8D?limit=25&after=123456789012345678" \
  -H "Authorization: Bearer YOUR_SESSION_TOKEN"
```

### Response

`200 OK`

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

### Errors

| Status | Description |
| --- | --- |
| `400` | Invalid message ID or `after` ID. |
| `401` | Not authenticated. |
| `404` | Message does not exist. |

## WebSocket events

| Event | When it fires |
| --- | --- |
| `MESSAGE_REACTION_ADD` | A user adds a reaction. The actor does not receive an echo because the REST response already covers the optimistic update. |
| `MESSAGE_REACTION_REMOVE` | A user removes their own reaction. |
| `MESSAGE_REACTION_REMOVE_EMOJI` | A moderator removes all reactions of one emoji. |
| `MESSAGE_REACTION_REMOVE_ALL` | A moderator clears all reactions on a message. |

Each event payload includes `user_id`, `message_id`, `channel_id`, and `emoji` (except `REMOVE_ALL`, which omits `emoji`).

## Related endpoints

*   [Messages](messages.md) — Send and manage messages.
*   [Channels](channels.md) — Channel management and permissions.
