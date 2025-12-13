# Avatars Module

Avatar and server icon storage and processing.

## Features

- Database storage (BLOB) for simplified backup
- Automatic image resizing
- Support for JPEG, PNG, GIF (animated), WebP
- Public access endpoints (no auth required for viewing)
- Animated GIF frame preservation

## Configuration

```yaml
avatars:
  max_size: 512           # Maximum dimension (width/height)
  max_file_size: 5242880  # 5MB before processing
  allowed_types:
    - image/jpeg
    - image/png
    - image/gif
    - image/webp
```

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/avatars/users/{id}` | Get user avatar |
| POST | `/avatars/users/@me` | Upload user avatar |
| DELETE | `/avatars/users/@me` | Delete user avatar |
| GET | `/avatars/servers/{id}` | Get server icon |
| POST | `/avatars/servers/{id}` | Upload server icon |
| DELETE | `/avatars/servers/{id}` | Delete server icon |

## Database Schema

### user_avatars

| Column | Type | Description |
|--------|------|-------------|
| id | BIGINT | Primary key |
| user_id | BIGINT | User ID (unique) |
| avatar_data | BYTEA | Image binary data |
| content_type | TEXT | MIME type |
| width | INTEGER | Width in pixels |
| height | INTEGER | Height in pixels |
| size | INTEGER | File size in bytes |
| checksum | TEXT | SHA-256 hash |
| animated | INTEGER | 1 if animated GIF |
| uploaded_at | BIGINT | Upload timestamp |

### server_icons

Same structure as user_avatars but with `server_id` instead of `user_id`.

## Processing Pipeline

1. Validate file type and size
2. Resize if exceeds max_size (preserves aspect ratio)
3. Optimize for web delivery
4. Preserve animated GIF frames
5. Store as BLOB in database
