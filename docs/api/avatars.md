# Avatars API

The Avatars API provides endpoints for managing user avatars and server icons. Avatars are stored directly in the database as BLOBs, with automatic resizing and format optimization.

## Features

- Automatic image resizing to configurable max dimensions (default: 512x512)
- Support for JPEG, PNG, GIF (including animated), and WebP formats
- Database storage for simplified backup and deployment
- Public access to avatar images (no authentication required for viewing)
- Animated GIF support with frame preservation

## Configuration

Avatar settings can be configured in `config.yaml`:

```yaml
avatars:
  # Maximum dimension for avatars (width and height)
  max_size: 512
  # Maximum file size before processing (5MB)
  max_file_size: 5242880
  # Allowed content types
  allowed_types:
    - image/jpeg
    - image/png
    - image/gif
    - image/webp
```

## Endpoints

### Get User Avatar

Retrieve a user's avatar image.

```
GET /api/v1/avatars/users/{user_id}
```

**Authentication:** Not required (public endpoint)

**Response:** Binary image data with appropriate `Content-Type` header

**Headers:**
- `Content-Type`: The image MIME type (e.g., `image/png`)
- `Cache-Control`: `public, max-age=86400`

**Errors:**
- `404` - Avatar not found

---

### Upload User Avatar

Upload or update the current user's avatar.

```
POST /api/v1/avatars/users/@me
```

**Authentication:** Required

**Content-Type:** `multipart/form-data`

**Request Body:**
- `file`: Image file (JPEG, PNG, GIF, or WebP)

**Response:**
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

**Errors:**
- `400` - Invalid file type or file too large
- `401` - Authentication required

---

### Delete User Avatar

Remove the current user's avatar.

```
DELETE /api/v1/avatars/users/@me
```

**Authentication:** Required

**Response:**
```json
{
  "success": true
}
```

---

### Get Server Icon

Retrieve a server's icon image.

```
GET /api/v1/avatars/servers/{server_id}
```

**Authentication:** Not required (public endpoint)

**Response:** Binary image data with appropriate `Content-Type` header

**Errors:**
- `404` - Icon not found

---

### Upload Server Icon

Upload or update a server's icon.

```
POST /api/v1/avatars/servers/{server_id}
```

**Authentication:** Required (must be server owner or have `MANAGE_SERVER` permission)

**Content-Type:** `multipart/form-data`

**Request Body:**
- `file`: Image file (JPEG, PNG, GIF, or WebP)

**Response:**
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

**Errors:**
- `400` - Invalid file type or file too large
- `401` - Authentication required
- `403` - Permission denied
- `404` - Server not found

---

### Delete Server Icon

Remove a server's icon.

```
DELETE /api/v1/avatars/servers/{server_id}
```

**Authentication:** Required (must be server owner or have `MANAGE_SERVER` permission)

**Response:**
```json
{
  "success": true
}
```

## Legacy Endpoint

The legacy avatar upload endpoint is still supported for backwards compatibility:

```
POST /api/v1/users/@me/avatar
```

This endpoint now uses the avatars module internally and returns the same response format.

## Image Processing

When an avatar is uploaded:

1. **Validation**: File type and size are checked against configuration
2. **Resizing**: If the image exceeds `max_size`, it's resized while maintaining aspect ratio
3. **Format optimization**: Images are optimized for web delivery
4. **Animated GIF handling**: All frames are preserved and resized together
5. **Storage**: The processed image is stored as a BLOB in the database

## Database Schema

Avatars are stored in two tables:

### user_avatars
- `id` (BIGINT): Primary key (Snowflake ID)
- `user_id` (BIGINT): User ID (unique)
- `avatar_data` (BYTEA): Image binary data
- `content_type` (TEXT): MIME type
- `width` (INTEGER): Image width in pixels
- `height` (INTEGER): Image height in pixels
- `size` (INTEGER): File size in bytes
- `checksum` (TEXT): SHA-256 hash
- `animated` (INTEGER): 1 if animated GIF
- `uploaded_at` (BIGINT): Upload timestamp

### server_icons
- `id` (BIGINT): Primary key (Snowflake ID)
- `server_id` (BIGINT): Server ID (unique)
- `icon_data` (BYTEA): Image binary data
- `content_type` (TEXT): MIME type
- `width` (INTEGER): Image width in pixels
- `height` (INTEGER): Image height in pixels
- `size` (INTEGER): File size in bytes
- `checksum` (TEXT): SHA-256 hash
- `animated` (INTEGER): 1 if animated GIF
- `uploaded_at` (BIGINT): Upload timestamp
