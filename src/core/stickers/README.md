# Stickers

The stickers module provides sticker packs and individual stickers with image validation, pack management, usage tracking, and content-based sticker suggestions. It supports default system packs, server-specific packs, and per-user personal packs.

## Overview

Use this module when you need to create sticker packs, add and manage stickers, and suggest relevant stickers based on message content. All mutations check the appropriate permissions and clean up backing media files on deletion.

## Setup

Initialize the manager with a connected database and the optional servers and messaging modules.

```python
from src.core.database import Database
from src.core import auth
from src.core import messaging
from src.core import servers
from src.core import stickers

db = Database()
db.connect()

auth.setup(db)
messaging.setup(db, auth)
servers.setup(db, auth, messaging)

stickers.setup(db, messaging, servers)
```

`StickersManager` also accepts an optional `media_module` for file-upload flows via `create_sticker_from_file`. Without the servers module, membership and permission checks are permissive; with it, they are enforced. See `manager.py:212`.

## Configuration

Settings are read from the global config under `stickers`. Defaults are loaded in `manager.py:48`:

| Key | Default | Description |
|---|---|---|
| `max_packs_per_server` | `50` | Maximum server sticker packs per server. |
| `max_packs_per_user` | `100` | Maximum personal packs per user. |
| `max_stickers_per_pack` | `50` | Maximum stickers per pack. |
| `max_sticker_size` | `524288` (512 KB) | Maximum file size in bytes. |
| `max_sticker_name_length` | `30` | Maximum sticker name length. |
| `max_pack_name_length` | `50` | Maximum pack name length. |
| `max_pack_description_length` | `200` | Maximum pack description length (truncated, not rejected). |
| `allowed_formats` | `["png", "apng", "json"]` | Allowed sticker formats. |
| `max_suggestions` | `10` | Upper bound for suggestion results. |

## Usage

### Create and manage packs

```python
from src.core import stickers
from src.core.stickers.models import PackType

# Create a server sticker pack (requires server.manage)
pack = stickers.create_pack(
    user_id=owner_id,
    name="Server Emotes",
    description="Custom emotes for our server",
    server_id=server_id,
    pack_type=PackType.SERVER,
)

# Create a personal pack (no server_id)
personal = stickers.create_pack(
    user_id=user_id,
    name="My Pack",
    pack_type=PackType.PERSONAL,
)

# Retrieve packs
pack = stickers.get_pack(pack_id, user_id)
server_packs = stickers.get_server_packs(user_id, server_id)
my_packs = stickers.get_user_packs(user_id)

# Update and delete
stickers.update_pack(user_id, pack_id, name="New Name", description="New description")
stickers.delete_pack(user_id, pack_id)
```

`create_pack` validates the pack name, enforces per-server or per-user limits, and persists `description_encrypted` (encrypted or plaintext depending on the `encryption.encrypt_descriptions` flag). See `manager.py:226`.

### Add and manage stickers

```python
from src.core.stickers.models import StickerFormat

# Add a sticker from a URL
sticker = stickers.add_sticker(
    user_id=owner_id,
    pack_id=pack.id,
    name="happy_pepe",
    format=StickerFormat.PNG,
    url="https://cdn.example.com/stickers/happy_pepe.png",
    size=245760,
    tags=["happy", "pepe", "smile"],
    related_emoji="😊",
    width=512,
    height=512,
)

# Add a sticker from raw bytes (validates magic bytes, cleans up on failure)
sticker = stickers.create_sticker_from_file(
    user_id=owner_id,
    pack_id=pack.id,
    name="happy_pepe",
    image_data=file_bytes,
    content_type="image/png",
    tags=["happy"],
    related_emoji="😊",
)

# Retrieve and update
sticker = stickers.get_sticker(sticker_id)
stickers_for_pack = stickers.get_pack_stickers(user_id, pack_id)
updated = stickers.update_sticker(user_id, sticker_id, name="very_happy_pepe", tags=["joy"])

# Remove a sticker (deletes backing media file)
stickers.delete_sticker(user_id, sticker_id)
# Legacy alias
stickers.remove_sticker(user_id, sticker_id)
```

Validation rules for `add_sticker`:

* `name` must match `^[a-zA-Z0-9_\-]+$` and not exceed `max_sticker_name_length`. See `manager.py:93`.
* `format` must be in `allowed_formats`. See `manager.py:422`.
* `size` must not exceed `max_sticker_size`.
* The pack must not exceed `max_stickers_per_pack`.

`create_sticker_from_file` additionally validates that the declared `content_type` matches the bytes detected by magic bytes (`PNG` magic for `image/png` and `image/apng`, JSON parse for `application/json`). Mismatched content is rejected to prevent stored-XSS via SVG or HTML masquerading as PNG. See `manager.py:54`.

### Track usage and get suggestions

```python
# Record that a sticker was sent in a message
usage = stickers.send_sticker(
    user_id=user_id, message_id=message_id, sticker_id=sticker_id
)

# Get suggestions based on message content
suggestions = stickers.get_sticker_suggestions(
    user_id=user_id,
    content="I'm so happy today!",
    server_id=server_id,
    limit=5,
)
for s in suggestions:
    print(f"{s.sticker.name}: {s.relevance_score}, matched: {s.matched_keywords}")
```

The suggestion algorithm is defined in `src/core/stickers/_discovery.py`:

| Signal | Score | Description |
|---|---|---|
| Exact name match in content | `1.0` | Sticker name appears as a substring. |
| Tag match in content words | `0.5` per tag | Each matching tag word. |
| Related emoji in content | `0.3` | Related emoji appears in content. |
| Usage popularity bonus | Up to `0.5` | Normalized by max usage in the candidate set. |

Results are sorted by relevance score in descending order.

## Data model

### StickerFormat (`models.py:11`)

| Value | Extension | Description |
|---|---|---|
| `PNG` | `.png` | Static image. |
| `APNG` | `.apng` | Animated PNG. |
| `LOTTIE` | `.json` | Lottie JSON animation. |

### PackType (`models.py:19`)

| Value | Access | Description |
|---|---|---|
| `DEFAULT` | All users | Built-in system packs. Cannot be deleted or modified. |
| `SERVER` | Server members | Server-specific packs with `server_id` set. |
| `PURCHASED` | Owner only | User-purchased packs. |
| `PERSONAL` | Owner only | Per-user packs with `server_id IS NULL`. Indexed by `pack_type` alone to avoid ambiguity with mis-typed server packs. |

### StickerPack (`models.py:37`)

| Field | Type | Description |
|---|---|---|
| `id` | `SnowflakeID` | Pack ID. |
| `name` | `str` | Validated pack name. |
| `description` | `str \| None` | Pack description (stored as `description_encrypted`). |
| `pack_type` | `PackType` | Pack type. |
| `server_id` | `SnowflakeID \| None` | Owning server, `None` for personal packs. |
| `created_by` | `SnowflakeID` | Creator. |
| `sticker_count` | `int` | Aggregated from `sticker_stickers`. |

### Sticker (`models.py:53`)

| Field | Type | Description |
|---|---|---|
| `id` | `SnowflakeID` | Sticker ID. |
| `pack_id` | `SnowflakeID` | Parent pack. |
| `name` | `str` | Validated name. |
| `format` | `StickerFormat` | File format. |
| `tags` | `list[str]` | Searchable tags (stored as JSON). |
| `related_emoji` | `str \| None` | Linked emoji. |
| `url` | `str` | Media URL. |
| `size` | `int` | File size. |
| `width` / `height` | `int \| None` | Dimensions. |
| `usage_count` | `int` | Aggregated from `sticker_usage`. |

## Permissions

| Operation | Required permission |
|---|---|
| Create, update, or delete a `SERVER` pack | `server.manage` on the server. |
| Add, update, or delete stickers in a `SERVER` pack | `server.manage` on the server. |
| Modify a personal or purchased pack | Caller must be `pack.created_by`. |
| View a `SERVER` pack or its stickers | Caller must be a server member. |

See `manager.py:212`, `manager.py:376`, and `manager.py:522`.

## Error handling

All exceptions inherit from `StickerError`. See `exceptions.py:7`.

| Exception | When it is raised |
|---|---|
| `PackNotFoundError` | Pack does not exist or is not visible to the caller. |
| `StickerNotFoundError` | Sticker does not exist. |
| `PackLimitError` | Maximum packs reached. Includes `max_allowed` and `current`. |
| `StickerLimitError` | Maximum stickers per pack reached. Includes `max_allowed` and `current`. |
| `InvalidStickerFormatError` | Format or content type not in `allowed_formats`. Includes `format` and `allowed`. |
| `StickerTooLargeError` | File exceeds `max_sticker_size`. Includes `max_size` and `actual_size`. |
| `InvalidStickerNameError` | Sticker name is empty, too long, or contains invalid characters. |
| `InvalidPackNameError` | Pack name is empty or too long. |
| `PermissionDeniedError` | Missing `server.manage` or not the pack owner. Includes `permission`. |
| `ServerNotFoundError` | Required `server_id` missing for server packs. |
| `MessageNotFoundError` | Message does not exist for `send_sticker`. |

Example:

```python
from src.core.stickers import (
    StickerError,
    PackNotFoundError,
    StickerNotFoundError,
    PackLimitError,
    StickerLimitError,
    InvalidStickerFormatError,
    StickerTooLargeError,
    InvalidStickerNameError,
    InvalidPackNameError,
    PermissionDeniedError,
)

try:
    stickers.add_sticker(user_id, pack_id, name, format, url, size)
except StickerTooLargeError as e:
    print(f"Sticker too large: {e.actual_size}/{e.max_size} bytes")
except InvalidStickerFormatError as e:
    print(f"Invalid format {e.format}, allowed: {e.allowed}")
except StickerLimitError as e:
    print(f"Pack full: {e.current}/{e.max_allowed} stickers")
except PermissionDeniedError as e:
    print(f"Missing permission: {e.permission}")
```

## Database schema

Tables are prefixed with `sticker_`:

| Table | Description |
|---|---|
| `sticker_packs` | Pack metadata, including `description_encrypted` and `pack_type`. |
| `sticker_stickers` | Individual stickers with tags and dimensions. |
| `sticker_usage` | Usage tracking joined to compute `usage_count` and suggestion ranking. |

Deleting a pack or sticker also deletes its backing media file through the media module (resolved by filename from the stored URL). See `manager.py:348`.

## Files

| File | Description |
|---|---|
| `manager.py` | `StickerManager` — pack and sticker CRUD, file upload, and media cleanup. |
| `_discovery.py` | `StickerUsageDiscoveryMixin` — usage recording and suggestion algorithm. |
| `models.py` | `StickerPack`, `Sticker`, `StickerFormat`, `PackType`, and related dataclasses. |
| `schema.py` | Pydantic schemas for request validation. |
| `exceptions.py` | `StickerError` hierarchy. |
| `__init__.py` | Module `setup` and public re-exports. |
