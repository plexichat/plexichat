# Emojis API

Manage custom emojis for a server. You can list, create, update, and delete
emojis that members use in messages and reactions.

All endpoints in this document are relative to `https://api.plexichat.com`.
For local development, use `http://localhost:8000`.

You must authenticate with `Authorization: Bearer <token>` for all emoji
endpoints.

## Overview

Each server can have up to 50 static emojis and 50 animated emojis. Emoji
names must be 2–32 characters and contain only lowercase letters, numbers, and
underscores. Image files must be PNG, GIF, or WebP and no larger than 256 KB.

Custom emojis use the following format in message content:

*   Static: `<:name:id>` — for example, `<:pepe:123456789012345678>`
*   Animated: `<a:name:id>` — for example, `<a:dance:123456789012345678>`

## Emoji resource

```json
{
  "id": "123456789012345678",
  "server_id": "987654321098765432",
  "name": "pepe",
  "animated": false,
  "url": "/media/image/2025/01/15/abc123.png",
  "available": true,
  "created_by": "111222333444555666",
  "uploader_username": "alice",
  "created_at": 1704067200000
}
```

### Properties

| Field | Type | Description |
| --- | --- | --- |
| `id` | string | Snowflake ID of the emoji. |
| `server_id` | string | ID of the owning server. |
| `name` | string | Emoji name. |
| `animated` | boolean | Whether the emoji is animated. |
| `url` | string | URL to the emoji image. |
| `available` | boolean | Whether the emoji is available for use. |
| `created_by` | string | ID of the user who created the emoji. |
| `uploader_username` | string or null | Username of the uploader. |
| `created_at` | integer | Creation timestamp in milliseconds. |

## List server emojis

Returns all custom emojis for a server. You must be a member of the server.

```
GET /api/v1/servers/{server_id}/emojis
```

### Response

`200 OK`

```json
[
  {
    "id": "123456789012345678",
    "server_id": "987654321098765432",
    "name": "pepe",
    "animated": false,
    "url": "/media/image/2025/01/15/abc123.png",
    "available": true,
    "created_by": "111222333444555666",
    "uploader_username": "alice",
    "created_at": 1704067200000
  }
]
```

### Errors

| Status | Description |
| --- | --- |
| `400` | Invalid server ID. |
| `401` | Not authenticated. |
| `403` | You are not a member of the server. |
| `500` | Reactions module not available. |

## Get emoji counts

Returns current emoji counts and limits for a server.

```
GET /api/v1/servers/{server_id}/emojis/counts
```

You must be a member of the server.

### Response

`200 OK`

```json
{
  "static": 25,
  "animated": 10,
  "max_static": 50,
  "max_animated": 50
}
```

| Field | Type | Description |
| --- | --- | --- |
| `static` | integer | Number of static emojis. |
| `animated` | integer | Number of animated emojis. |
| `max_static` | integer | Maximum static emojis allowed. |
| `max_animated` | integer | Maximum animated emojis allowed. |

### Errors

| Status | Description |
| --- | --- |
| `400` | Invalid server ID. |
| `401` | Not authenticated. |
| `403` | You are not a member of the server. |

## Get a single emoji

Returns details for a specific emoji.

```
GET /api/v1/servers/{server_id}/emojis/{emoji_id}
```

### Response

`200 OK` with an emoji resource.

### Errors

| Status | Description |
| --- | --- |
| `400` | Invalid emoji or server ID. |
| `401` | Not authenticated. |
| `404` | Emoji does not exist or does not belong to this server. |

## Create an emoji

Creates a new custom emoji.

```
POST /api/v1/servers/{server_id}/emojis
Content-Type: multipart/form-data
```

Requires the `emojis.manage` permission. The `server.manage` permission also
satisfies this check.

### Request

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `name` | string | Yes | Emoji name, 2–32 lowercase letters, numbers, and underscores. |
| `image` | file | Yes | Image file: PNG, GIF, or WebP. Max 256 KB. |

### Example request

```bash
curl -X POST https://api.plexichat.com/api/v1/servers/987654321098765432/emojis \
  -H "Authorization: Bearer YOUR_SESSION_TOKEN" \
  -F "name=pepe" \
  -F "image=@pepe.png"
```

### Response

`200 OK` with the created emoji resource.

### Errors

| Status | Description |
| --- | --- |
| `400` | Invalid name, file too large, or unsupported format. |
| `401` | Not authenticated. |
| `403` | Missing `emojis.manage` permission. |
| `409` | An emoji with this name already exists. |

## Update an emoji

Renames an existing emoji.

```
PATCH /api/v1/servers/{server_id}/emojis/{emoji_id}
Content-Type: application/json
```

Requires the `emojis.manage` permission. The `server.manage` permission also
satisfies this check.

### Request body

```json
{
  "name": "happy_pepe"
}
```

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `name` | string | Yes | New emoji name. |

### Response

`200 OK` with the updated emoji resource.

### Errors

| Status | Description |
| --- | --- |
| `400` | Invalid name. |
| `401` | Not authenticated. |
| `403` | Missing `emojis.manage` permission. |
| `404` | Emoji does not exist. |
| `409` | An emoji with this name already exists. |

## Delete an emoji

Permanently deletes a custom emoji.

```
DELETE /api/v1/servers/{server_id}/emojis/{emoji_id}
```

Requires the `emojis.manage` permission. The `server.manage` permission also
satisfies this check.

### Response

```json
{
  "success": true
}
```

### Errors

| Status | Description |
| --- | --- |
| `400` | Invalid server or emoji ID. |
| `401` | Not authenticated. |
| `403` | Missing `emojis.manage` permission. |
| `404` | Emoji does not exist. |

## Use custom emojis

### In messages

Include the emoji markup in the message content:

```json
{
  "content": "Hello <:pepe:123456789012345678>!"
}
```

### In reactions

Use the emoji identifier when you add a reaction. URL-encode the identifier:

```
PUT /api/v1/channels/{channel_id}/messages/{message_id}/reactions/<:pepe:123456789012345678>
```

## Limits

| Limit | Value |
| --- | --- |
| Static emojis per server | 50 |
| Animated emojis per server | 50 |
| File size | 256 KB |
| Name length | 2–32 characters |

## Supported formats

| Format | Extension | Animated |
| --- | --- | --- |
| PNG | `.png` | No |
| GIF | `.gif` | Yes |
| WebP | `.webp` | Depends on file content |

## WebSocket events

### GUILD_EMOJIS_UPDATE

Dispatched to all server members when emojis are added, updated, or removed.

```json
{
  "op": 0,
  "t": "GUILD_EMOJIS_UPDATE",
  "d": {
    "guild_id": "987654321098765432",
    "emojis": [
      {
        "id": "123456789012345678",
        "name": "pepe",
        "animated": false,
        "url": "/media/image/2025/01/15/abc123.png",
        "available": true
      }
    ]
  }
}
```
