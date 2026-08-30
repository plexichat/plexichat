# Reaction manager

Manage reactions and custom emoji for Plexichat messages.

## Overview

The reaction manager handles reactions on messages, custom emoji lifecycle, and permission-aware access control. It validates emoji input, enforces limits, filters blocked users, and stores image data through the media module.

Use this module when you need to add or remove reactions, query aggregated reaction counts, or create and manage server-scoped custom emoji.

## Architecture

The manager is composed from mixins combined through multiple inheritance in `base.py`:

| File | Class | Responsibility |
|------|-------|----------------|
| `__init__.py` | — | Re-exports `ReactionManager` from `base.py` |
| `base.py` | `ReactionBase`, `ReactionManager` | Initializes dependencies, loads configuration, provides database helpers and composition |
| `permissions.py` | `ReactionPermissionsMixin` | Participant checks, server permission checks, block filtering |
| `validation.py` | `ReactionValidationMixin` | Emoji format validation, Unicode validation, custom emoji name validation |
| `reactions.py` | `ReactionOpsMixin` | Add, remove, and query reactions; batch operations |
| `emojis.py` | `EmojiOpsMixin` | Custom emoji CRUD, image upload, table migration |

All mixins combine into `ReactionManager` in `base.py:118`. `ReactionBase:21` runs `_migrate_emoji_table` once at construction to add columns (`url`, `size`, `width`, `height`, `created_by`, `available`) to legacy `react_custom_emoji` tables.

## Before you begin

Initialize `ReactionManager` with required dependencies.

```python
from plexichat.src.core.reactions.manager import ReactionManager

manager = ReactionManager(
    db,
    messaging_module=messaging,
    servers_module=servers,
    relationships_module=relationships,
    media_module=media,
)
```

Dependencies:

* `src.core.base.BaseManager` — Database access and ID generation (`base.py:4`).
* `src.core.messaging` — Message and conversation lookup (`base.py:66`, `reactions.py:29`).
* `src.core.servers` — Permission checks for custom emoji management (`emojis.py:87`).
* `src.core.relationships` — Block filtering (`reactions.py:45`).
* `src.core.media` — Image storage and validation (`emojis.py:147`).

## Manage reactions

### Add a reaction

Call `add_reaction` with a Unicode emoji or a custom emoji reference. The manager validates that you are a participant, checks server permissions, blocks reactions between blocked users, and enforces per-message and per-user limits.

```python
# Add a Unicode reaction
reaction = manager.add_reaction(user_id=1, message_id=42, emoji="🎉")

# Add a custom emoji reaction
reaction = manager.add_reaction(user_id=1, message_id=42, emoji=":blob_happy:")
```

`reactions.py:26` raises `MessageNotFoundError` if the message does not exist or you are not a participant, `PermissionDeniedError` if you lack `messages.add_reactions`, `UserBlockedError` if either user has blocked the other, `ReactionExistsError` on duplicates, and `ReactionLimitError` when limits are exceeded.

### Remove a reaction

```python
# Remove your own reaction
manager.remove_reaction(user_id=1, message_id=42, emoji="🎉")

# Remove all reactions on a message (requires messages.manage or owner/admin)
count = manager.remove_all_reactions(user_id=1, message_id=42)

# Remove all reactions for a specific emoji (requires messages.manage)
count = manager.remove_all_reactions_for_emoji(user_id=1, message_id=42, emoji="🎉")
```

`reactions.py:161` and `reactions.py:204` fail closed when the conversation is deleted or permissions cannot be verified.

### Query reactions

```python
# Get aggregated counts for a message, filtered for blocked users
summary = manager.get_reactions(user_id=1, message_id=42)
# summary.reactions is a list of ReactionCount, summary.total_count is the total

# List users who reacted with an emoji (paginated)
users = manager.get_reaction_users(user_id=1, message_id=42, emoji="🎉", limit=100)

# Get only your reactions on a message
mine = manager.get_user_reactions(user_id=1, message_id=42)

# Batch fetch for multiple messages
batch = manager.get_reactions_batch(user_id=1, message_ids=[42, 43, 44])

# Counts
total = manager.get_reaction_count(message_id=42, viewer_id=1)
by_emoji = manager.get_reaction_count_by_emoji(message_id=42, emoji="🎉", viewer_id=1)
```

`get_reactions` (`reactions.py:327`) and `get_reaction_users` (`reactions.py:382`) exclude reactions from users you have blocked. `get_reaction_users` returns an empty list for invalid emoji instead of raising, to preserve read compatibility with legacy data (`reactions.py:399`).

## Manage custom emoji

### Create a custom emoji

```python
emoji = manager.create_custom_emoji(
    user_id=1,
    server_id=100,
    name="blob_happy",
    image_data=image_bytes,
    content_type="image/png",
)
```

`emojis.py:78` validates name length, file size, MIME type, and image dimensions, checks `server.manage` permission, enforces static and animated limits, and uploads through the media module when available. It requires `name` to be 2-32 characters and rejects images larger than 1024×1024.

### Update and delete custom emoji

```python
# Rename
updated = manager.update_custom_emoji(user_id=1, emoji_id=5, name="blob_excited")

# Delete (also removes reactions that use this emoji)
manager.delete_custom_emoji(user_id=1, emoji_id=5)
```

`update_custom_emoji` (`emojis.py:217`) and `delete_custom_emoji` (`emojis.py:266`) require `server.manage` and raise `CustomEmojiNotFoundError` or `EmojiNameExistsError` as appropriate.

### List and inspect emoji

```python
# Get one emoji
emoji = manager.get_custom_emoji(emoji_id=5)

# List all emoji for a server
emojis = manager.get_server_custom_emojis(server_id=100)

# Check quota usage
counts = manager.get_emoji_counts(server_id=100)
# {"static": 12, "animated": 3, "max_static": 50, "max_animated": 50}
```

## Handle errors

Reaction operations raise exceptions from `src.core.reactions.exceptions`:

| Exception | When it occurs |
|-----------|----------------|
| `PermissionDeniedError` | You lack permission for the conversation or server |
| `UserBlockedError` | Either user has blocked the other (`reactions.py:45`) |
| `InvalidEmojiError`, `ValueError` | Malformed emoji string or name (`validation.py`) |
| `CustomEmojiNotFoundError` | Custom emoji does not exist or not in server |
| `ReactionExistsError` | Duplicate reaction |
| `ReactionNotFoundError` | No matching reaction to remove |
| `ReactionLimitError` | Per-message or per-user unique reaction limit exceeded |
| `EmojiLimitError`, `EmojiFileSizeError`, `InvalidEmojiFileError` | Quota or file validation failure (`emojis.py:78`) |
| `EmojiNameExistsError` | Duplicate emoji name in server |

Example:

```python
from plexichat.src.core.reactions.exceptions import InvalidEmojiError, ReactionLimitError

try:
    manager.add_reaction(user_id=1, message_id=42, emoji="invalid")
except InvalidEmojiError as e:
    print(f"Invalid emoji: {e}")
except ReactionLimitError as e:
    print(f"Limit reached: {e}")
```

Blocked users are filtered silently in read paths; `add_reaction` and `remove_reaction` raise `UserBlockedError` instead.

## Configuration

Configure reactions through `utils.config` under the `reactions` and `emojis` keys. `base.py:51` merges these with defaults:

| Key | Default | Description |
|-----|---------|-------------|
| `max_reactions_per_message` | `20` | Maximum distinct emoji per message. Overridden per server by `srv_servers.max_reactions_per_message` (`reactions.py:73`). |
| `max_unique_reactions_per_user` | `50` | Maximum distinct emoji a single user can add to a message (`reactions.py:81`). |
| `max_users_per_reaction_page` | `100` | Maximum users returned by `get_reaction_users` (`reactions.py:404`). |
| `max_emoji_size` | `262144` (256 KiB) | Maximum image bytes for custom emoji (`emojis.py:94`). |
| `emoji_allowed_formats` | `["image/png", "image/gif", "image/webp"]` | Allowed MIME types (`emojis.py:102`). |
| `emoji_min_name_length` | `2` | Minimum emoji name length. |
| `emoji_max_name_length` | `32` | Maximum emoji name length. |
| `max_emojis_per_server` | `50` | Maximum static emoji per server (`emojis.py:63`). |
| `max_animated_emojis_per_server` | `50` | Maximum animated emoji per server (`emojis.py:57`). |
