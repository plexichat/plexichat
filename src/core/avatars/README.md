# Avatars Module

Avatar and server icon storage and processing.

## Features

- Database storage (BLOB) for simplified backup
- Automatic image resizing with PIL/Pillow
- Support for JPEG, PNG, GIF (animated), WebP
- Redis caching with checksum-based ETags
- Content type detection via magic bytes (prevents MIME spoofing)
- Animated GIF frame preservation
- Default SVG placeholder generation with stable color seeding

## Configuration

```yaml
avatars:
  max_size: 512           # Maximum dimension (width/height)
  max_file_size: 5242880  # 5MB before processing
  max_pixels: 178956970   # Decompression bomb protection (~178MP)
  max_dimension: 16384    # Max width/height before rejection
  allowed_types:
    - image/jpeg
    - image/png
    - image/gif
    - image/webp
```

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v1/avatars/users/{user_id}` | Get user avatar |
| POST | `/api/v1/avatars/users/@me` | Upload user avatar |
| DELETE | `/api/v1/avatars/users/@me` | Delete user avatar |
| GET | `/api/v1/avatars/servers/{server_id}` | Get server icon |
| POST | `/api/v1/avatars/servers/{server_id}` | Upload server icon |
| DELETE | `/api/v1/avatars/servers/{server_id}` | Delete server icon |

## Database Schema

### user_avatars

| Column | Type | Description |
|--------|------|-------------|
| id | BIGINT | Primary key (snowflake) |
| user_id | BIGINT | User ID (unique) |
| avatar_data | BYTEA | Image binary data |
| content_type | TEXT | MIME type |
| width | INTEGER | Width in pixels |
| height | INTEGER | Height in pixels |
| size | INTEGER | File size in bytes |
| checksum | TEXT | SHA-256 hash |
| animated | INTEGER | 1 if animated GIF |
| uploaded_at | BIGINT | Upload timestamp (ms) |

### server_icons

Same structure as user_avatars but with `server_id` instead of `user_id`.

## Processing Pipeline

1. Validate content type against allowed list
2. Check file size against max_file_size
3. Detect actual content type from magic bytes (prevent MIME spoofing)
4. Decompression bomb protection (max_pixels, max_dimension)
5. Resize if exceeds max_size (preserves aspect ratio via LANCZOS)
6. Handle animated GIFs: resize all frames preserving duration/loop
7. Optimize for web delivery (JPEG quality 90, PNG optimize, WebP quality 90)
8. Store as BLOB in database
9. Cache binary data and checksum in Redis

## Security Features

- **MIME spoofing protection**: Actual content type verified from magic bytes
- **Decompression bomb protection**: Max pixel and dimension limits
- **Type restriction**: Only JPEG, PNG, GIF, WebP allowed
- **Size limit**: 5MB max before processing

## Redis Caching

Avatar binary data and checksums are cached in Redis for faster serving:

| Key Pattern | TTL |
|-------------|-----|
| `user_avatar_bin:{user_id}` | 1 hour |
| `user_avatar_meta:{user_id}` | 1 hour |
| `server_icon_bin:{server_id}` | 1 hour |
| `server_icon_meta:{server_id}` | 1 hour |

## Default SVG Placeholder

When no avatar is set, a deterministic SVG is generated based on user ID:
- Color selected from a palette via SHA-256 hash of user seed
- Initials displayed centered on the SVG
