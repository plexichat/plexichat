# Reaction Manager

## Purpose
Manages message reactions and custom emoji, including add/remove/reaction queries,
emoji CRUD, upload handling, and permission-gated access control.

The reaction manager is split across multiple files using the mixin pattern:

- **`__init__.py`** — Thin re-export of `ReactionManager` from `base.py`
- **`base.py`** — `ReactionBase` (core setup, config, DB helpers) + `ReactionManager` composition class
- **`permissions.py`** — `ReactionPermissionsMixin`: participant checks, server permissions, block filtering
- **`validation.py`** — `ReactionValidationMixin`: emoji validation, unicode checks, name validation
- **`reactions.py`** — `ReactionOpsMixin`: add/remove/query reactions, batch operations
- **`emojis.py`** — `EmojiOpsMixin`: custom emoji CRUD, upload, migration

All mixins are combined into `ReactionManager` via multiple inheritance in `base.py`.

## Usage

```python
from src.core.reactions.manager import ReactionManager

rm = ReactionManager(db, messaging_module=messaging, servers_module=servers,
                     relationships_module=relationships, media_module=media)

# Add a reaction
reaction = rm.add_reaction(user_id=1, message_id=42, emoji="🎉")

# Add a custom emoji reaction
reaction = rm.add_reaction(user_id=1, message_id=42, emoji=":custom_emoji:", custom_emoji_id=5)

# Remove a reaction
rm.remove_reaction(user_id=1, message_id=42, emoji="🎉")

# Get reactions for a message
reactions = rm.get_reactions(message_id=42)

# Create a custom emoji for a server
emoji = rm.create_custom_emoji(server_id=1, name="blob_happy", image_data=b"...", uploader_id=1)
```

## Error Handling

Reaction operations raise exceptions from `src.core.reactions.exceptions` and domain modules:

- `PermissionError` — User lacks permission to react in the conversation/channel.
- `ValueError` — Invalid emoji string, malformed custom emoji name, or empty reaction target.
- Blocked users silently fail (no reaction added, no exception raised).
- Custom emoji upload may raise `FileTooLargeError` and `InvalidFileTypeError` from the media module.

```python
try:
    reaction = rm.add_reaction(user_id=1, message_id=42, emoji="invalid")
except ValueError as e:
    print(f"Invalid emoji: {e}")
```

## Dependencies
- `src.core.base.BaseManager` — Database access, ID generation.
- `src.core.messaging` — Message lookup and conversation resolution.
- `src.core.servers` — Server permission checks for custom emoji management.
- `src.core.relationships` — Block list filtering (blocked users cannot react to each other's messages).
- `src.core.media` — Emoji image storage and validation (size, format).

## Configuration
- `max_reactions_per_message`: 20 (default).
- `max_emoji_size`: 262144 bytes (256KB).
- `emoji_allowed_formats`: `["image/png", "image/gif", "image/webp"]`.
- `emoji_max_name_length`: 32, `emoji_min_name_length`: 2.
- `max_emojis_per_server`: 50, `max_animated_emojis_per_server`: 50.
