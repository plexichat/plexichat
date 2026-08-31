# Avatar manager

The avatar manager handles avatar and server icon storage, image processing, caching, and default SVG generation. It uses a mixin-based architecture where each domain concern is isolated into a composable class, and the `AvatarManager` composer combines them via MRO.

## Architecture

```
AvatarManager (composer)
├── AvatarSetupMixin      — module initialization and configuration
├── AvatarCachingMixin    — Valkey binary caching with bounded LRU
├── AvatarTablesMixin     — database table creation
├── AvatarProcessingMixin — image validation, detection, and resizing
├── AvatarUserMixin       — user avatar CRUD operations
├── AvatarServerMixin     — server icon CRUD operations
├── AvatarDefaultsMixin   — default SVG placeholder generation
└── BaseManager           — shared base class
```

The `AvatarProtocol` class defines the interface contract for all mixins using `cast()` delegation to the `super()` chain.

## Key files

| File | Purpose |
|------|---------|
| `__init__.py` | Package entry point; exports `AvatarManager` |
| `manager.py` | `AvatarManager` — composed manager class |
| `protocol.py` | `AvatarProtocol` — interface definition for mixin delegation |
| `setup.py` | `AvatarSetupMixin` — initialization and config access |
| `caching.py` | `AvatarCachingMixin` — Valkey-based binary caching |
| `tables.py` | `AvatarTablesMixin` — database schema creation |
| `processing.py` | `AvatarProcessingMixin` — image validation and resizing |
| `user.py` | `AvatarUserMixin` — user avatar upload, retrieve, delete |
| `server.py` | `AvatarServerMixin` — server icon upload, retrieve, delete |
| `defaults.py` | `AvatarDefaultsMixin` — SVG placeholder generation |

## Usage

```python
from src.core.avatars.manager import AvatarManager

manager = AvatarManager()
manager.setup(db)

# Upload a user avatar
result = manager.upload_user_avatar(user_id=123, image_data=bytes, content_type="image/png")
# Returns: {"id": "789", "user_id": "123", "url": "/api/v1/avatars/users/123", ...}

# Retrieve avatar data (cached)
data = manager.get_user_avatar_data(user_id=123)
# Returns: (avatar_bytes, "image/png", "sha256checksum") or None

# Delete avatar
manager.delete_user_avatar(user_id=123)

# Server icons work identically
manager.upload_server_icon(server_id=456, image_data=bytes, content_type="image/jpeg")
manager.get_server_icon_data(server_id=456)
manager.delete_server_icon(server_id=456)

# Generate a default SVG placeholder
svg = manager.generate_default_svg(seed="user123", initials="AB")
```

## Configuration

Read from `config.get("avatars", {})`:

| Key | Default | Description |
|-----|---------|-------------|
| `max_size` | `512` | Maximum avatar dimension in pixels |
| `max_file_size` | `5242880` (5 MB) | Maximum file size in bytes |
| `allowed_types` | `["image/jpeg", "image/png", "image/gif", "image/webp"]` | Allowed MIME types |
| `default_colors` | `["#6d86d9", "#5ea381", "#c99563", "#5f9ccf", "#8c79c8", "#c27ba2"]` | SVG background colors |
| `max_pixels` | `178956970` | Maximum pixel count (decompression bomb protection) |
| `max_dimension` | `16384` | Maximum single dimension |

## Image processing

The processing mixin handles:

- **Content type detection** — validates MIME types against magic bytes to prevent spoofing (`src/core/avatars/manager/processing.py:20`)
- **Decompression bomb protection** — enforces `max_pixels` and `max_dimension` limits (`src/core/avatars/manager/processing.py:55`)
- **Animated GIF support** — resizes all frames while preserving animation (`src/core/avatars/manager/processing.py:106`)
- **Animated WebP support** — resizes all frames with quality 90 (`src/core/avatars/manager/processing.py:139`)
- **Format preservation** — static GIFs keep GIF format instead of being re-encoded to JPEG (`src/core/avatars/manager/processing.py:207`)

## Caching

The caching mixin uses a bounded per-process LRU key set (default 1024 entries) backed by Valkey for binary storage. Keys expire after 10 minutes by default (`src/core/avatars/manager/caching.py:24`). The LRU evicts the oldest entry when the cap is reached (`src/core/avatars/manager/caching.py:54`).

Cache keys follow the pattern:

- `user_avatar_bin:{user_id}` — avatar binary data
- `user_avatar_meta:{user_id}` — content type and checksum
- `user_avatar_checksum:{user_id}` — ETag support
- `server_icon_bin:{server_id}`, `server_icon_meta:{server_id}`, `server_icon_checksum:{server_id}` — server equivalents

## Related code

- `src/core/base.py` — `BaseManager` parent class
- `src/core/avatars/schema.py` — canonical table DDL (used by both manager and migrations)
- `src/core/database/__init__.py` — `get_valkey_client`, `valkey_available`
- `src/utils/encryption.py` — `generate_snowflake_id` for avatar IDs
