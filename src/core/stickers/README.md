# Stickers Module

Sticker pack and custom sticker system for Plexichat API supporting default packs, server custom packs, user purchased packs, sticker upload with validation, and intelligent sticker suggestions.

## Features

- Sticker packs (default, server custom, user purchased)
- Sticker upload with image validation (PNG/APNG/Lottie JSON)
- Sticker metadata (name, tags, related emoji)
- Sticker suggestions based on message content
- Sticker usage tracking
- Pack management (create, add stickers, remove, delete)
- Server-specific sticker packs with permissions
- Format validation and size limits

## Setup

```python
from src.core.database import Database
from src.core import auth
from src.core import messaging
from src.core import servers
from src.core import stickers

# Initialize database
db = Database()
db.connect()

# Initialize dependencies
auth.setup(db)
messaging.setup(db, auth)
servers.setup(db, auth, messaging)

# Initialize stickers
stickers.setup(db, messaging, servers)
```

## Usage

### Sticker Packs

```python
from src.core import stickers

# Create a server sticker pack
pack = stickers.create_pack(
    user_id=owner_id,
    name="Server Emotes",
    description="Custom emotes for our server",
    server_id=server_id,
    pack_type=stickers.PackType.SERVER
)

# Get a pack
pack = stickers.get_pack(pack_id, user_id)

# Get all packs for a server
packs = stickers.get_server_packs(user_id, server_id)

# Delete a pack
stickers.delete_pack(user_id, pack_id)
```

### Stickers

```python
# Add a sticker to a pack
sticker = stickers.add_sticker(
    user_id=owner_id,
    pack_id=pack.id,
    name="happy_pepe",
    format=stickers.StickerFormat.PNG,
    url="https://cdn.example.com/stickers/happy_pepe.png",
    size=245760,
    tags=["happy", "pepe", "smile"],
    related_emoji="smile",
    width=512,
    height=512
)

# Get a sticker
sticker = stickers.get_sticker(sticker_id)

# Get all stickers in a pack
pack_stickers = stickers.get_pack_stickers(user_id, pack_id)

# Remove a sticker
stickers.remove_sticker(user_id, sticker_id)
```

### Sticker Usage

```python
# Send a sticker in a message
usage = stickers.send_sticker(
    user_id=user_id,
    message_id=message_id,
    sticker_id=sticker_id
)
```

### Sticker Suggestions

```python
# Get sticker suggestions based on message content
suggestions = stickers.get_sticker_suggestions(
    user_id=user_id,
    content="I'm so happy today!",
    server_id=server_id,
    limit=5
)

for suggestion in suggestions:
    print(f"{suggestion.sticker.name}: {suggestion.relevance_score}")
    print(f"Matched: {', '.join(suggestion.matched_keywords)}")
```

## Configuration

Settings in `config/config.yaml` under `stickers`:

```yaml
stickers:
  max_packs_per_server: 50
  max_stickers_per_pack: 50
  max_sticker_size: 524288  # 512KB
  max_sticker_name_length: 30
  max_pack_name_length: 50
  max_pack_description_length: 200
  allowed_formats:
    - png
    - apng
    - json  # Lottie animations
  max_suggestions: 10
```

## Sticker Formats

| Format | Description | Extension |
|--------|-------------|-----------|
| PNG | Static image | .png |
| APNG | Animated PNG | .apng |
| LOTTIE | Lottie JSON animation | .json |

## Pack Types

| Type | Description | Access |
|------|-------------|--------|
| DEFAULT | Built-in packs | All users |
| SERVER | Server-specific packs | Server members |
| PURCHASED | User-purchased packs | Owner only |

## Permission Integration

For server sticker packs, the module checks:

| Permission | Description |
|------------|-------------|
| server.manage | Required to create, edit, delete server sticker packs |

## Sticker Suggestions

The suggestion algorithm considers:
- Exact name matches in content (score: 1.0)
- Tag matches in content words (score: 0.5 per tag)
- Related emoji in content (score: 0.3)
- Usage popularity bonus (up to 0.5)

Results are sorted by relevance score descending.

## Error Handling

All sticker errors inherit from `StickerError`:

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

## Database Schema

Tables (prefixed with `sticker_`):
- `sticker_packs` - Sticker pack metadata
- `sticker_stickers` - Individual stickers
- `sticker_usage` - Usage tracking

## Testing

```bash
pytest src/tests/stickers/ -v
```

## Integration with Messaging

Stickers can be attached to messages similar to attachments. The messaging module should handle sticker rendering and display.

## Best Practices

1. **Naming**: Use descriptive, searchable names (e.g., "happy_cat" not "img001")
2. **Tags**: Add relevant tags for better suggestions
3. **Related Emoji**: Link stickers to emoji for context-aware suggestions
4. **Size**: Keep stickers under 512KB for fast loading
5. **Format**: Use PNG for static, APNG for simple animations, Lottie for complex animations
