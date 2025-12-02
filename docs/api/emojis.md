# Emojis API

Custom emoji management endpoints for server-specific emojis.

## Overview

Custom emojis allow servers to have their own unique emoji that members can use in messages and reactions. Each server can have up to 50 static emojis and 50 animated emojis.

## Emoji Format

Custom emojis are referenced using the format:
- Static: `<:name:id>` (e.g., `<:pepe:123456789012345678>`)
- Animated: `<a:name:id>` (e.g., `<a:dance:123456789012345678>`)

## Endpoints

### List Server Emojis

```
GET /api/v1/servers/{server_id}/emojis
```

Returns all custom emojis for a server.

**Response:**
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
    "created_at": 1704067200000
  }
]
```

### Get Emoji Counts

```
GET /api/v1/servers/{server_id}/emojis/counts
```

Returns current emoji counts and limits for a server.

**Response:**
```json
{
  "static": 25,
  "animated": 10,
  "max_static": 50,
  "max_animated": 50
}
```

### Get Single Emoji

```
GET /api/v1/servers/{server_id}/emojis/{emoji_id}
```

Returns details for a specific emoji.

**Response:**
```json
{
  "id": "123456789012345678",
  "server_id": "987654321098765432",
  "name": "pepe",
  "animated": false,
  "url": "/media/image/2025/01/15/abc123.png",
  "available": true,
  "created_by": "111222333444555666",
  "created_at": 1704067200000
}
```

### Create Emoji

```
POST /api/v1/servers/{server_id}/emojis
Content-Type: multipart/form-data
```

Creates a new custom emoji. Requires `server.manage` permission.

**Form Fields:**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| name | string | Yes | Emoji name (2-32 lowercase alphanumeric + underscores) |
| image | file | Yes | Image file (PNG, GIF, or WebP, max 256KB) |

**Response:**
```json
{
  "id": "123456789012345678",
  "server_id": "987654321098765432",
  "name": "pepe",
  "animated": false,
  "url": "/media/image/2025/01/15/abc123.png",
  "available": true,
  "created_by": "111222333444555666",
  "created_at": 1704067200000
}
```

**Errors:**
| Code | Description |
|------|-------------|
| 400 | Invalid name, file too large, or invalid format |
| 403 | Missing `server.manage` permission |
| 409 | Emoji with this name already exists |

### Update Emoji

```
PATCH /api/v1/servers/{server_id}/emojis/{emoji_id}
Content-Type: application/json
```

Updates an emoji's name. Requires `server.manage` permission.

**Request Body:**
```json
{
  "name": "happy_pepe"
}
```

**Response:**
```json
{
  "id": "123456789012345678",
  "server_id": "987654321098765432",
  "name": "happy_pepe",
  "animated": false,
  "url": "/media/image/2025/01/15/abc123.png",
  "available": true,
  "created_by": "111222333444555666",
  "created_at": 1704067200000
}
```

### Delete Emoji

```
DELETE /api/v1/servers/{server_id}/emojis/{emoji_id}
```

Deletes a custom emoji. Requires `server.manage` permission.

**Response:**
```json
{
  "success": true
}
```

## Using Custom Emojis

### In Messages

Include the emoji format in message content:

```json
{
  "content": "Hello <:pepe:123456789012345678>!"
}
```

### In Reactions

Use the emoji format when adding reactions:

```
PUT /api/v1/channels/{channel_id}/messages/{message_id}/reactions/<:pepe:123456789012345678>
```

Note: The emoji string must be URL-encoded.

## Limits

| Limit | Default | Description |
|-------|---------|-------------|
| Static emojis per server | 50 | Maximum non-animated emojis |
| Animated emojis per server | 50 | Maximum animated emojis |
| File size | 256KB | Maximum image file size |
| Name length | 2-32 | Emoji name character limits |

## Supported Formats

| Format | Extension | Animated |
|--------|-----------|----------|
| PNG | .png | No |
| GIF | .gif | Yes |
| WebP | .webp | Depends on content |

## WebSocket Events

### GUILD_EMOJIS_UPDATE

Dispatched when emojis are added, updated, or removed from a server.

```json
{
  "op": 0,
  "t": "GUILD_EMOJIS_UPDATE",
  "d": {
    "server_id": "987654321098765432",
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
