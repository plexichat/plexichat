# Search API

Search for messages, users, and servers. You use this API to power discovery, autocomplete, and diagnostics.

All endpoints in this document are relative to `https://api.plexichat.com`.
For local development, use `http://localhost:8000`.

You must authenticate with `Authorization: Bearer <token>` for all search endpoints.

Search respects access control. You only receive results you have permission to view, and search does not bypass channel or server visibility.

Results are paginated with cursors. Do not assume a global count is available; use `next_cursor` to fetch the next page.

For interactive search fields, debounce input and paginate rather than widening the same request.

## Search messages

Search accessible messages. You can filter by conversation, server, channel, and author, and you can use advanced query filters such as `from:user` and `has:image`.

```
GET /api/v1/search/messages
```

### Query parameters

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| `query` | string | Yes | Search query. Supports filter syntax such as `from:alice`, `in:general`, `before:2024-01-01`, `has:image`, `mentions:bob`, `pinned:true`, `reaction:👍`, and exact phrases in quotes. |
| `conversation_id` | string | No | Snowflake ID. Limit results to a conversation. |
| `server_id` | string | No | Snowflake ID. Limit results to a server. |
| `channel_id` | string | No | Snowflake ID. Limit results to a channel. |
| `author_id` | string | No | Snowflake ID. Limit results to an author. |
| `limit` | integer | No | Results per page. `1`–`100`, default `25`. |
| `cursor` | string | No | Pagination cursor from the previous response. |

### Example request

```bash
curl -X GET "https://api.plexichat.com/api/v1/search/messages?query=hello%20from%3Aalice&limit=25" \
  -H "Authorization: Bearer YOUR_SESSION_TOKEN"
```

### Response

`200 OK`

```json
{
  "results": [
    {
      "id": "123456789012345678",
      "channel_id": "123456789012345678",
      "author_id": "123456789012345678",
      "content": "Hello world",
      "created_at": 1704067200,
      "score": 1.2
    }
  ],
  "next_cursor": "eyJvZmZzZXQiOjI1fQ"
}
```

| Field | Type | Description |
| --- | --- | --- |
| `results` | array | Array of message results for the current page. |
| `next_cursor` | string or null | Opaque cursor for the next page. `null` when no more results. |

### Errors

| Status | Description |
| --- | --- |
| `400` | Invalid query or malformed ID filter. |
| `401` | Not authenticated. |
| `503` | Search backend unavailable. |

## Search users

Search for users across the platform or within a specific server. You can search by username or display name (case-insensitive, partial matches). An exact email address also resolves to its account.

```
GET /api/v1/search/users
```

### Query parameters

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| `query` | string | Yes | Username, display name, or email to search for. |
| `server_id` | string | No | Snowflake ID. Limit results to members of a server. |
| `limit` | integer | No | Results per page. `1`–`100`, default `25`. |
| `cursor` | string | No | Pagination cursor. |

### Response

`200 OK` with a stable envelope that uses `items` as the canonical result list. `results` is a legacy alias that always mirrors `items`.

```json
{
  "items": [
    {
      "id": "1234567890",
      "user_id": "1234567890",
      "username": "alice",
      "display_name": "Alice",
      "avatar_url": "/api/v1/avatars/users/1234567890",
      "badges": [],
      "score": 1.0
    }
  ],
  "total": 1,
  "results": [
    {
      "id": "1234567890",
      "user_id": "1234567890",
      "username": "alice",
      "display_name": "Alice",
      "avatar_url": "/api/v1/avatars/users/1234567890",
      "badges": [],
      "score": 1.0
    }
  ],
  "next_cursor": null
}
```

| Field | Type | Description |
| --- | --- | --- |
| `items` | array | Canonical result list. Each entry includes `id`, `user_id`, `username`, `display_name`, `avatar_url`, `badges`, and `score`. |
| `total` | integer | Number of results in the current page. This is not a global count. |
| `results` | array | Legacy alias of `items`. Always identical to `items`. |
| `next_cursor` | string or null | Cursor for the next page. |

The API bulk-resolves live usernames and avatar URLs once per request, so `username` reflects the directory rather than a stale index entry. Client-side bulk resolution is not required.

### Errors

| Status | Description |
| --- | --- |
| `400` | Invalid query. |
| `401` | Not authenticated. |
| `503` | Search backend unavailable. |

## Search servers

Search public servers by name, description, or tags.

```
GET /api/v1/search/servers
```

### Query parameters

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| `query` | string | Yes | Name, description, or tags to search for. |
| `category` | string | No | Filter by server category. |
| `limit` | integer | No | Results per page. `1`–`100`, default `25`. |
| `cursor` | string | No | Pagination cursor. |

### Response

`200 OK`

```json
{
  "results": [
    {
      "server_id": "123456789012345678",
      "name": "Gaming Hub",
      "description": "A place for gamers",
      "icon_url": "https://cdn.example.com/icons/123.png",
      "member_count": 1200,
      "category": "gaming",
      "score": 0.95
    }
  ],
  "next_cursor": null
}
```

### Errors

| Status | Description |
| --- | --- |
| `400` | Invalid query. |
| `401` | Not authenticated. |
| `503` | Search backend unavailable. |

## Get search suggestions

Get autocomplete suggestions for a partial query. You can use this to surface filter names and recent search history.

```
GET /api/v1/search/suggestions
```

### Query parameters

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| `q` | string | Yes | Partial query to generate suggestions for. |
| `limit` | integer | No | Maximum suggestions. `1`–`20`, default `10`. |

### Example request

```bash
curl -X GET "https://api.plexichat.com/api/v1/search/suggestions?q=from%3Aa&limit=5" \
  -H "Authorization: Bearer YOUR_SESSION_TOKEN"
```

### Response

`200 OK`

```json
{
  "suggestions": ["from:alice", "from:alex", "has:image"]
}
```

| Field | Type | Description |
| --- | --- | --- |
| `suggestions` | array | Ordered list of suggestion strings. May mix filter names and history entries. |

### Errors

| Status | Description |
| --- | --- |
| `401` | Not authenticated. |
| `503` | Search backend unavailable. |

## Get search health

Get backend health and index diagnostics. You can use this for client or Docker diagnostics.

```
GET /api/v1/search/health
```

### Response

`200 OK`

If the search module is not enabled:

```json
{
  "enabled": false,
  "backend": null,
  "indexer": null,
  "healthy": false
}
```

If enabled:

```json
{
  "enabled": true,
  "backend": "sqlite_fts5",
  "indexer": "SqliteIndexer",
  "healthy": true,
  "stats": {
    "message_count": 12000,
    "user_count": 500
  }
}
```

| Field | Type | Description |
| --- | --- | --- |
| `enabled` | boolean | Whether the search module is enabled. |
| `backend` | string or null | Backend identifier, such as `sqlite_fts5`, `elasticsearch`, or `meilisearch`. |
| `indexer` | string or null | Indexer class name. |
| `healthy` | boolean | Whether the indexer health check passed. |
| `stats` | object | Index statistics, if available. |

## List public servers for discovery

List public servers for the discovery directory.

```
GET /api/v1/search/discovery/servers
```

### Query parameters

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| `category` | string | No | Filter by server category. |
| `limit` | integer | No | Maximum servers to return. `1`–`100`, default `25`. |
| `offset` | integer | No | Offset for pagination, `>= 0`, default `0`. |

### Response

`200 OK`

```json
{
  "results": [
    {
      "id": "123456789012345678",
      "server_id": "123456789012345678",
      "name": "Gaming Hub",
      "description": "A place for gamers",
      "category": "gaming",
      "member_count": 1200
    }
  ],
  "next_cursor": null
}
```

## Get server discovery categories

Get available categories for server discovery.

```
GET /api/v1/search/discovery/categories
```

### Response

`200 OK`

```json
{
  "categories": [
    {
      "id": "gaming",
      "name": "Gaming",
      "description": "Gaming communities",
      "icon": "🎮",
      "server_count": 42
    }
  ]
}
```

| Field | Type | Description |
| --- | --- | --- |
| `categories` | array | Array of category objects. Each has `id`, `name`, `description`, `icon`, and `server_count`. |

## Related endpoints

*   [Messages](messages.md) — Message contents and channel history.
*   [Servers](servers.md) — Server creation and membership.
