# Avatars

This guide covers user avatars and server icons. Images are stored as BLOBs in the database with automatic resizing and format handling.

**Base URL**: `https://api.plexichat.com/api/v1`

For development, use `http://localhost:8000/api/v1`.

All endpoints in this guide are prefixed with `/api/v1/avatars` unless noted. The router is defined in `src/api/routes/avatars.py`.

## Features

- Automatic resizing to a configurable maximum dimension (default `512`×`512`).
- Support for JPEG, PNG, GIF (including animated), and WebP.
- Database storage for simplified backup and deployment.
- Public read access for image bytes; authentication required for writes.
- Animated GIF frame preservation during resize.
- `ETag` and `Cache-Control` headers for conditional requests (`304 Not Modified`).
- Gateway events (`USER_UPDATE` and `GUILD_UPDATE`) when avatars or icons change.

## Configuration

Configure avatars in `config.yaml` or `src/config_defaults.py:790`:

```yaml
avatars:
  # Maximum dimension in pixels (width and height)
  max_size: 512
  # Maximum file size before processing
  max_file_size: 5242880  # 5 MiB
  # Allowed content types
  allowed_types:
    - image/jpeg
    - image/png
    - image/gif
    - image/webp
```

Related `media` limits for other upload types are in `src/config_defaults.py:184`.

## Authentication

| Operation | Authentication |
|---|---|
| `GET /api/v1/avatars/users/{user_id}` | Not required (public) |
| `POST /api/v1/avatars/users/@me` | Required (`Bearer` or `Bot` token) |
| `DELETE /api/v1/avatars/users/@me` | Required |
| `GET /api/v1/avatars/servers/{server_id}` | Not required (public) |
| `POST /api/v1/avatars/servers/{server_id}` | Required (server owner or `server.manage` permission) |
| `DELETE /api/v1/avatars/servers/{server_id}` | Required (server owner or `server.manage` permission) |

## Endpoints

### Get a user avatar

Retrieve a user's avatar image. Returns a default SVG avatar when no custom avatar is set.

```
GET /api/v1/avatars/users/{user_id}
```

**Authentication**: Not required.

**Response**: Binary image data with an appropriate `Content-Type` header (`image/png`, `image/jpeg`, `image/gif`, `image/webp`, or `image/svg+xml` for generated avatars).

**Response headers**

| Header | Value |
|---|---|
| `Content-Type` | Image MIME type |
| `Cache-Control` | `public, max-age=86400` (or `public, max-age=3600` for generated SVG) |
| `ETag` | `"<sha256-checksum>"` |
| `Access-Control-Allow-Origin` | `*` |
| `Cross-Origin-Resource-Policy` | `cross-origin` |

Supports `If-None-Match` for conditional requests. When the checksum matches, the server returns `304 Not Modified` without a body (`src/api/routes/avatars.py:130`).

**Errors**

| Status | Meaning |
|---|---|
| `400` | Invalid user ID |
| `404` | Only for server icons with no fallback; user avatars return a generated SVG instead of `404` |
| `500` | Avatars module not available |

### Upload your avatar

Upload or replace the current user's avatar.

```
POST /api/v1/avatars/users/@me
```

**Authentication**: Required.

**Content-Type**: `multipart/form-data`

**Request body**

| Field | Type | Description |
|---|---|---|
| `file` | file | Image file (`image/jpeg`, `image/png`, `image/gif`, or `image/webp`) |

**Response `200 OK`**

```json
{
  "success": true,
  "avatar_url": "/api/v1/avatars/users/123456789",
  "width": 512,
  "height": 512,
  "size": 45678,
  "animated": false
}
```

Schema: `src/api/schemas/avatars.py:8` (`AvatarUploadResponse`). On success the server invalidates the avatar checksum cache and emits a `USER_UPDATE` gateway event.

**Errors**

| Status | Meaning |
|---|---|
| `400` | File missing, not an image, or invalid image data |
| `401` | Authentication required |
| `500` | Upload failed |

### Delete your avatar

Remove the current user's avatar.

```
DELETE /api/v1/avatars/users/@me
```

**Authentication**: Required.

**Response `200 OK`**

```json
{
  "success": true
}
```

The server clears the checksum cache and emits a `USER_UPDATE` event with an empty `avatar_url`.

### Get a server icon

Retrieve a server's icon. Returns a default SVG icon when no custom icon is set.

```
GET /api/v1/avatars/servers/{server_id}
```

**Authentication**: Not required.

**Response**: Binary image data with the same caching headers as user avatars.

Supports `If-None-Match` → `304 Not Modified` (`src/api/routes/avatars.py:487`).

**Errors**

| Status | Meaning |
|---|---|
| `400` | Invalid server ID |
| `404` | Icon not found and SVG generation failed |
| `500` | Avatars or database module not available |

### Upload a server icon

Upload or replace a server's icon.

```
POST /api/v1/avatars/servers/{server_id}
```

**Authentication**: Required. Must be the server owner or have `server.manage` permission (`src/api/routes/avatars.py:650`).

**Content-Type**: `multipart/form-data`

**Request body**

| Field | Type | Description |
|---|---|---|
| `file` | file | Image file (`image/jpeg`, `image/png`, `image/gif`, or `image/webp`) |

**Response `200 OK`**

```json
{
  "success": true,
  "icon_url": "/api/v1/avatars/servers/987654321",
  "width": 512,
  "height": 512,
  "size": 34567,
  "animated": false
}
```

Schema: `src/api/schemas/avatars.py:21` (`IconUploadResponse`). On success the server emits a `GUILD_UPDATE` gateway event (`src/api/routes/avatars.py:728`).

**Errors**

| Status | Meaning |
|---|---|
| `400` | Invalid server ID, file missing, or invalid image |
| `401` | Authentication required |
| `403` | Permission denied (`MANAGE_SERVER` required) |
| `404` | Server not found |
| `500` | Upload failed or permission check failed |

### Delete a server icon

Remove a server's icon.

```
DELETE /api/v1/avatars/servers/{server_id}
```

**Authentication**: Required. Must be the server owner or have `server.manage` permission.

**Response `200 OK`**

```json
{
  "success": true
}
```

The server clears the icon cache and emits a `GUILD_UPDATE` event.

## Legacy endpoint

The legacy avatar upload endpoint remains supported for compatibility:

```
POST /api/v1/users/@me/avatar
```

This endpoint now delegates to the avatars module internally and returns the same `AvatarUploadResponse` format.

## Image processing

When an avatar or icon is uploaded, the server does the following:

1. **Validate** the file type and size against `avatars.allowed_types` and `avatars.max_file_size`.
2. **Resize** the image if it exceeds `avatars.max_size`, preserving aspect ratio.
3. **Optimize** the image for web delivery.
4. **Preserve animation** for animated GIFs by resizing all frames together.
5. **Store** the processed image as a BLOB in the database and cache its SHA-256 checksum for `ETag` generation.

Invalid image data returns `400` with `Invalid image file` instead of `500`.

## Database schema

### user_avatars

| Column | Type | Description |
|---|---|---|
| `id` | `BIGINT` | Primary key (Snowflake ID) |
| `user_id` | `BIGINT` | User ID (unique) |
| `avatar_data` | `BYTEA` | Image binary data |
| `content_type` | `TEXT` | MIME type |
| `width` | `INTEGER` | Image width in pixels |
| `height` | `INTEGER` | Image height in pixels |
| `size` | `INTEGER` | File size in bytes |
| `checksum` | `TEXT` | SHA-256 hash (used for `ETag`) |
| `animated` | `INTEGER` | `1` if animated GIF |
| `uploaded_at` | `BIGINT` | Upload timestamp |

### server_icons

| Column | Type | Description |
|---|---|---|
| `id` | `BIGINT` | Primary key (Snowflake ID) |
| `server_id` | `BIGINT` | Server ID (unique) |
| `icon_data` | `BYTEA` | Image binary data |
| `content_type` | `TEXT` | MIME type |
| `width` | `INTEGER` | Image width in pixels |
| `height` | `INTEGER` | Image height in pixels |
| `size` | `INTEGER` | File size in bytes |
| `checksum` | `TEXT` | SHA-256 hash |
| `animated` | `INTEGER` | `1` if animated GIF |
| `uploaded_at` | `BIGINT` | Upload timestamp |

Checksum caches are stored in-process (capped at 4096 entries) and in Valkey under `user_avatar_checksum:{user_id}` and `server_icon_checksum:{server_id}`.

## Caching and gateway behavior

- Successful `GET` responses include `ETag` and `Cache-Control`. Clients should send `If-None-Match` to avoid re-downloading unchanged images.
- Uploads and deletions invalidate both the in-process and Valkey checksum caches (`src/api/routes/avatars.py:48`).
- Avatar changes emit `USER_UPDATE`; server icon changes emit `GUILD_UPDATE` so connected clients can update without a full refetch.
