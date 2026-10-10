# Reactions

Add emoji reactions to messages with support for Unicode emoji, custom server emoji, permissions, and block filtering.

## Overview

The reactions module provides an API for adding, removing, and querying reactions on messages. It supports Unicode emoji and custom server emoji (`<:name:id>` for static, `<a:name:id>` for animated), with permission checks for server channels and filtering for blocked users.

Custom emoji are uploaded through the media module and stored with metadata such as URL, dimensions, and file size.

## Before you begin

Initialize the database and the modules that reactions depends on. The reactions module requires a connected database and, optionally, messaging, servers, relationships, and media modules.

```python
from src.core.database import Database
from src.core import auth, messaging, servers, relationships, media, reactions

db = Database()
db.connect()

auth.setup(db)
messaging.setup(db, auth)
servers.setup(db, auth, messaging)
relationships.setup(db, auth, servers)
media.setup(db, messaging)

reactions.setup(db, messaging, servers, relationships, media)
```

## Add a reaction

```python
from src.core import reactions

# Unicode emoji
reaction = reactions.add_reaction(user_id=1, message_id=123, emoji="thumbsup")

# Custom server emoji
reaction = reactions.add_reaction(user_id=1, message_id=123, emoji="<:custom:456>")
```

Reactions enforces one reaction per emoji per user, a configurable maximum of unique emojis per message, and a per-user limit. If the message is in a server channel, the caller must have `messages.add_reactions`.

## Remove a reaction

```python
# Remove your own reaction
reactions.remove_reaction(user_id=1, message_id=123, emoji="thumbsup")
```

Only the user who added a reaction can remove it. To remove another user's reaction, use the moderator methods described below.

## Get reactions on a message

```python
msg_reactions = reactions.get_reactions(user_id=1, message_id=123)

for r in msg_reactions.reactions:
    print(f"{r.emoji}: {r.count} reactions (me: {r.me})")

print(f"Total: {msg_reactions.total_count}")
```

Counts and the `me` flag exclude users blocked by or blocking the viewer. Results are grouped by emoji and ordered by the earliest reaction.

### Batch queries

Use batch methods to avoid N+1 queries when loading message lists:

```python
batch = reactions.get_reactions_batch(user_id=1, message_ids=[123, 456, 789])
# Returns {message_id: [{"emoji": ..., "count": ..., "me": ..., "is_custom": ...}]}

# Alias with identical semantics
batch = reactions.get_reactions_bulk(user_id=1, message_ids=[123, 456, 789])
```

## Get users who reacted

```python
users = reactions.get_reaction_users(
    user_id=1,
    message_id=123,
    emoji="thumbsup",
    limit=50,
)

for user in users:
    print(f"User {user.user_id} reacted at {user.reacted_at}")

# Paginate with after_user_id
more = reactions.get_reaction_users(
    user_id=1,
    message_id=123,
    emoji="thumbsup",
    limit=50,
    after_user_id=users[-1].user_id,
)
```

The `limit` is capped by `max_users_per_reaction_page` (default 100). Blocked users are excluded.

## Moderate reactions

These actions require `messages.manage` on server channels, or owner/admin role on direct conversations:

```python
# Remove all reactions from a message
count = reactions.remove_all_reactions(moderator_id, message_id)

# Remove all reactions of a specific emoji
count = reactions.remove_all_reactions_for_emoji(moderator_id, message_id, "thumbsup")
```

## Manage custom emoji

### Create an emoji

```python
with open("pepe.png", "rb") as f:
    image_data = f.read()

emoji = reactions.create_custom_emoji(
    user_id=owner_id,
    server_id=server_id,
    name="pepe",
    image_data=image_data,
    content_type="image/png",
)

print(f"Emoji created: <:{emoji.name}:{emoji.id}>")
print(f"URL: {emoji.url}")
```

Requires `server.manage`. The name must be 2–32 characters, unique per server, and the file must be `image/png`, `image/gif`, or `image/webp` under 256 KB by default. Limits are 50 static and 50 animated emojis per server by default.

### Update and delete

```python
# Rename
emoji = reactions.update_custom_emoji(user_id=owner_id, emoji_id=emoji.id, name="happy_pepe")

# Delete
reactions.delete_custom_emoji(owner_id, emoji.id)
```

### List emoji

```python
# All emoji for a server
emojis = reactions.get_server_custom_emojis(server_id)
for e in emojis:
    print(f":{e.name}: - {e.url} (animated: {e.animated})")

# Counts and limits
counts = reactions.get_emoji_counts(server_id)
print(f"Static: {counts['static']}/{counts['max_static']}")
print(f"Animated: {counts['animated']}/{counts['max_animated']}")

# Single emoji by ID
emoji = reactions.get_custom_emoji(emoji_id)
```

### Use custom emoji in reactions

```
Static:   <:name:id>   (for example, <:pepe:123456789>)
Animated: <a:name:id>  (for example, <a:dance:123456789>)
```

```python
reactions.add_reaction(user_id, message_id, "<:pepe:123456789>")
```

## Configuration

Configure limits in `config/config.yaml`:

```yaml
reactions:
  max_reactions_per_message: 20
  max_unique_reactions_per_user: 50
  max_users_per_reaction_page: 100

emojis:
  max_emojis_per_server: 50
  max_animated_emojis_per_server: 50
  max_emoji_size: 262144  # 256 KB
  emoji_allowed_formats:
    - image/png
    - image/gif
    - image/webp
  emoji_max_name_length: 32
  emoji_min_name_length: 2
```

Per-server overrides for `max_reactions_per_message` are read from `srv_servers` when available.

## Permissions

| Permission | Required for |
|------------|--------------|
| `messages.add_reactions` | Add reactions in server channels |
| `messages.manage` | Remove all reactions or remove reactions by emoji in server channels |
| `server.manage` | Create, update, or delete custom emoji |

For direct conversations, the conversation owner or a participant with `owner` or `admin` role can perform moderator actions.

## Blocked user behavior

* Blocked users cannot react to each other's messages.
* Reaction counts exclude reactions from blocked users.
* User lists exclude blocked users.

## Error handling

All reaction errors inherit from `ReactionError`:

```python
from src.core.reactions import (
    ReactionError,
    MessageNotFoundError,
    ReactionNotFoundError,
    ReactionExistsError,
    InvalidEmojiError,
    CustomEmojiNotFoundError,
    ReactionLimitError,
    PermissionDeniedError,
    EmojiLimitError,
    EmojiNameExistsError,
    InvalidEmojiNameError,
    EmojiFileSizeError,
    InvalidEmojiFileError,
)

try:
    reactions.add_reaction(user_id, message_id, emoji)
except ReactionExistsError:
    print("Already reacted with this emoji")
except ReactionLimitError as e:
    print(f"Max {e.max_allowed} unique reactions reached")
except PermissionDeniedError as e:
    print(f"Missing permission: {e.permission}")
except InvalidEmojiError:
    print("Invalid emoji format")

try:
    reactions.create_custom_emoji(user_id, server_id, name, image_data, content_type)
except EmojiLimitError as e:
    print(f"Server has reached max {e.max_allowed} emojis")
except EmojiNameExistsError:
    print("Emoji with this name already exists")
except InvalidEmojiNameError:
    print("Invalid emoji name")
except EmojiFileSizeError as e:
    print(f"File too large: {e.actual_size}/{e.max_size} bytes")
except InvalidEmojiFileError:
    print("Invalid file format")
```

Reads with legacy or invalid emoji values return empty results rather than raising `InvalidEmojiError`, so stored data does not break queries.

## Database schema

Tables are prefixed with `react_`:

| Table | Description |
|-------|-------------|
| `react_reactions` | User reactions on messages |
| `react_custom_emoji` | Server custom emoji definitions |

### `react_custom_emoji` columns

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER | Snowflake ID (primary key) |
| `server_id` | INTEGER | Server that owns the emoji |
| `name` | TEXT | Emoji name, unique per server |
| `animated` | INTEGER | `1` if animated, `0` if static |
| `url` | TEXT | URL to the emoji image |
| `size` | INTEGER | File size in bytes |
| `width` | INTEGER | Image width, or `NULL` |
| `height` | INTEGER | Image height, or `NULL` |
| `created_by` | INTEGER | User ID of the creator |
| `available` | INTEGER | `1` if available for use |
| `created_at` | INTEGER | Creation timestamp |

## Module structure

| File | Class | Responsibility |
|------|-------|---------------|
| `manager/__init__.py` | — | Re-exports `ReactionManager` |
| `manager/base.py` | `ReactionBase` + `ReactionManager` | Core setup, config, and DB helpers |
| `manager/permissions.py` | `ReactionPermissionsMixin` | Participant checks, server permissions, and block filtering |
| `manager/validation.py` | `ReactionValidationMixin` | Emoji validation and name validation |
| `manager/reactions.py` | `ReactionOpsMixin` | Add, remove, and query reactions |
| `manager/emojis.py` | `EmojiOpsMixin` | Custom emoji CRUD and image upload |

## Testing

```bash
pytest src/tests/reactions/ -v
```
