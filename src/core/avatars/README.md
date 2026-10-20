# Avatars module

## Overview

The avatars module stores and processes user avatars and server icons. It validates uploads, resizes images, preserves animated GIF and WebP frames, caches results in Valkey, and serves binary data with checksum-based ETags.

Storage is in the database as BLOBs — see `plexichat/src/core/avatars/schema.py:11` — for simplified backup and restore.

## Features

*   Database BLOB storage for avatars and server icons.
*   Automatic resizing with Pillow, preserving aspect ratio through `LANCZOS`.
*   Support for `image/jpeg`, `image/png`, `image/gif` (animated), and `image/webp`.
*   Valkey caching with checksum-based ETags.
*   Content-type detection from magic bytes to prevent MIME spoofing — see `plexichat/src/core/avatars/manager/processing.py:20`.
*   Decompression-bomb protection using pixel and dimension limits.
*   Animated GIF and WebP frame preservation, including duration and loop.
*   Deterministic SVG placeholder generation with stable color seeding.

## Prerequisites

*   Pillow for image processing. If Pillow is not installed, the module stores the original bytes without resizing — see `plexichat/src/core/avatars/manager/processing.py:48`.
*   A configured `Database` instance. Call `avatars.setup(db)` before use — see `plexichat/src/core/avatars/__init__.py:52`.

## Configuration

Configure avatars under the `avatars` key — see `plexichat/src/config_defaults.py:790` and `plexichat/src/core/avatars/manager/setup.py:14`:

```yaml
avatars:
  max_size: 512           # Maximum dimension in pixels (width or height)
  max_file_size: 5242880  # 5 MB before processing
  allowed_types:
    - image/jpeg
    - image/png
    - image/gif
    - image/webp
  default_colors:
    - "#e94560"
    - "#4ade80"
    - "#fbbf24"
    - "#60a5fa"
    - "#a78bfa"
    - "#f472b6"
```

Global server keys use these paths — see `plexichat/src/config_defaults.py`:

*   `server.host`, `server.port`, `server.reload` — see `plexichat/src/config_defaults.py:70`. Do not use `api.host` or `api.port`.
*   `api.api_prefix` — default `/api/v1` — see `plexichat/src/config_defaults.py:130`. Do not use `api.prefix`.
*   `tls` — see `plexichat/src/config_defaults.py:618`. Do not use `api.ssl`.

Image-processing limits are also enforced in code — see `plexichat/src/core/avatars/manager/processing.py:54`:

*   `max_pixels` — default `178956970` (~178 MP).
*   `max_dimension` — default `16384` pixels per side.

## API endpoints

Health and gateway paths for client integration:

*   WebSocket gateway: `/gateway` — see `plexichat/src/api/websocket/gateway.py:261`.
*   Readiness probe: `GET /api/v1/ready` — see `plexichat/src/api/routes/health.py:18`.
*   Deep health check: `GET /api/v1/health` — see `plexichat/src/api/routes/health.py:46`.

Avatar endpoints are mounted under the API prefix (`/api/v1`):

| Method | Endpoint | Description |
| --- | --- | --- |
| `GET` | `/api/v1/avatars/users/{user_id}` | Get a user avatar. |
| `POST` | `/api/v1/avatars/users/@me` | Upload the current user avatar. |
| `DELETE` | `/api/v1/avatars/users/@me` | Delete the current user avatar. |
| `GET` | `/api/v1/avatars/servers/{server_id}` | Get a server icon. |
| `POST` | `/api/v1/avatars/servers/{server_id}` | Upload a server icon. |
| `DELETE` | `/api/v1/avatars/servers/{server_id}` | Delete a server icon. |

## Database schema

### `user_avatars`

Defined in `plexichat/src/core/avatars/schema.py:13`.

| Column | Type | Description |
| --- | --- | --- |
| `id` | `BIGINT` | Primary key (snowflake). |
| `user_id` | `BIGINT` | User ID (unique). |
| `avatar_data` | `BLOB` | Image binary data. |
| `content_type` | `TEXT` | MIME type. |
| `width` | `INTEGER` | Width in pixels. |
| `height` | `INTEGER` | Height in pixels. |
| `size` | `INTEGER` | File size in bytes. |
| `checksum` | `TEXT` | SHA-256 hash. |
| `animated` | `INTEGER` | `1` if animated GIF or WebP. |
| `uploaded_at` | `BIGINT` | Upload timestamp in milliseconds. |

### `server_icons`

Defined in `plexichat/src/core/avatars/schema.py:27`. Same structure as `user_avatars` with `server_id` instead of `user_id` and `icon_data` instead of `avatar_data`.

Indexes: `idx_user_avatars_user` and `idx_server_icons_server`.

## Processing pipeline

Order is important for security and correctness — see `plexichat/src/core/avatars/manager/processing.py:40`:

1.  Validate `content_type` against the allowed list — see `plexichat/src/core/avatars/manager/processing.py:15`.
2.  Check file size against `max_file_size`.
3.  Detect actual content type from magic bytes; reject MIME spoofing — see `plexichat/src/core/avatars/manager/processing.py:20`.
4.  Enforce `max_pixels` and `max_dimension` before decoding — see `plexichat/src/core/avatars/manager/processing.py:54`.
5.  Resize if either dimension exceeds `max_size`, preserving aspect ratio with `LANCZOS`.
6.  Handle animated GIF and WebP: resize all frames and preserve `duration` and `loop`.
7.  Optimize for delivery: JPEG quality 90, PNG `optimize=true`, WebP quality 90.
8.  Store as BLOB and cache binary data and checksum in Valkey.

## Security

*   **MIME spoofing protection** — The actual type is detected from magic bytes. If it differs from the claimed type, the detected type is used.
*   **Decompression-bomb protection** — Images with excessive pixels or dimensions are rejected before processing.
*   **Type restriction** — Only `image/jpeg`, `image/png`, `image/gif`, and `image/webp` are allowed.
*   **Size limit** — Uploads larger than `max_file_size` are rejected.

## Valkey caching

Binary data and checksums are cached for 1 hour — see `plexichat/src/core/avatars/manager/caching.py`:

| Key pattern | TTL |
| --- | --- |
| `user_avatar_bin:{user_id}` | 1 hour |
| `user_avatar_meta:{user_id}` | 1 hour |
| `server_icon_bin:{server_id}` | 1 hour |
| `server_icon_meta:{server_id}` | 1 hour |

## Default SVG placeholder

When no avatar is set, the module generates a deterministic SVG — see `plexichat/src/core/avatars/manager/defaults.py`:

*   A color is selected from `avatars.default_colors` using a SHA-256 hash of the user seed.
*   Initials are rendered centered on the SVG.

## Gateway opcodes and intents

If you update avatars in real time over the gateway, use these values:

*   Opcodes — see `plexichat/src/api/websocket/opcodes.py:40`: `50 RATCHET_UPDATE`, `60 ARTIFACT_SUBSCRIBE`, `61 ARTIFACT_UNSUBSCRIBE`, `62 ARTIFACT_OP`, `63 ARTIFACT_SYNC`, `64 SUM_SUBSCRIBE`, `65 SUM_UNSUBSCRIBE`, `66 SUM_OP`, `67 SUM_SYNC`, `68 ARTIFACT_OP_REJECTED`.
*   Intents — see `plexichat/src/core/events/types.py:120`: `1<<20 ARTIFACTS` and `1<<21 SUMS`.
