# Reactions Module

Message reaction system for Plexichat API supporting Unicode emoji and custom server emoji with permission checks and block filtering.

## Features

- Add/remove reactions to messages
- Unicode emoji support
- Custom emoji support (server-specific, format: `<:name:id>` or `<a:name:id>` for animated)
- Custom emoji upload with image storage
- Reaction counts per emoji
- User lists for each reaction (paginated)
- One reaction per emoji per user
- Configurable max unique reactions per message (default 20)
- Configurable max emojis per server (default 50 static, 50 animated)
- Permission checks for server channels
- Blocked user filtering (users cannot see each other's reactions)
- Moderator actions (remove all reactions, remove specific emoji reactions)

## Setup

```python
from src.core.database import Database
from src.core import auth
from src.core import messaging
from src.core import servers
from src.core import relationships
from src.core import media
from src.core import reactions

# Initialize database
db = Database()
db.connect()

# Initialize dependencies
auth.setup(db)
messaging.setup(db, auth)
servers.setup(db, auth, messaging)
relationships.setup(db, auth, servers)
media.setup(db, messaging)

# Initialize reactions with media module for emoji uploads
reactions.setup(db, messaging, servers, relationships, media)
```

## Usage

### Add Reaction

```python
from src.core import reactions

# Add Unicode emoji reaction
reaction = reactions.add_reaction(
    user_id=1,
    message_id=123,
    emoji="thumbsup"
)

# Add custom emoji reaction (server-specific)
reaction = reactions.add_reaction(
    user_id=1,
    message_id=123,
    emoji="<:custom:456>"
)
```

### Remove Reaction

```python
# Remove own reaction
reactions.remove_reaction(user_id=1, message_id=123, emoji="thumbsup")
```

### Get Reactions

```python
# Get all reactions on a message
msg_reactions = reactions.get_reactions(user_id=1, message_id=123)

for r in msg_reactions.reactions:
    print(f"{r.emoji}: {r.count} reactions (me: {r.me})")

print(f"Total: {msg_reactions.total_count}")
```

### Get Users Who Reacted

```python
# Get users who reacted with specific emoji
users = reactions.get_reaction_users(
    user_id=1,
    message_id=123,
    emoji="thumbsup",
    limit=50
)

for user in users:
    print(f"User {user.user_id} reacted at {user.reacted_at}")

# Pagination
more_users = reactions.get_reaction_users(
    user_id=1,
    message_id=123,
    emoji="thumbsup",
    limit=50,
    after_user_id=users[-1].user_id
)
```

### Moderator Actions

```python
# Remove all reactions from a message
count = reactions.remove_all_reactions(moderator_id, message_id)
print(f"Removed {count} reactions")

# Remove all reactions of specific emoji
count = reactions.remove_all_reactions_for_emoji(moderator_id, message_id, "thumbsup")
print(f"Removed {count} thumbsup reactions")
```

### Custom Emoji

```python
# Create custom emoji for server (with image upload)
with open("pepe.png", "rb") as f:
    image_data = f.read()

emoji = reactions.create_custom_emoji(
    user_id=owner_id,
    server_id=server_id,
    name="pepe",
    image_data=image_data,
    content_type="image/png"
)

print(f"Emoji created: <:{emoji.name}:{emoji.id}>")
print(f"URL: {emoji.url}")

# Update emoji name
emoji = reactions.update_custom_emoji(
    user_id=owner_id,
    emoji_id=emoji.id,
    name="happy_pepe"
)

# Get server's custom emojis
emojis = reactions.get_server_custom_emojis(server_id)
for e in emojis:
    print(f":{e.name}: - {e.url} (animated: {e.animated})")

# Get emoji counts and limits
counts = reactions.get_emoji_counts(server_id)
print(f"Static: {counts['static']}/{counts['max_static']}")
print(f"Animated: {counts['animated']}/{counts['max_animated']}")

# Delete custom emoji
reactions.delete_custom_emoji(owner_id, emoji.id)
```

### Using Custom Emoji in Messages

Custom emojis can be used in messages and reactions using the format:
- Static: `<:name:id>` (e.g., `<:pepe:123456789>`)
- Animated: `<a:name:id>` (e.g., `<a:dance:123456789>`)

```python
# React with custom emoji
reactions.add_reaction(user_id, message_id, "<:pepe:123456789>")
```

## Configuration

Settings in `config/config.yaml`:

```yaml
reactions:
  max_reactions_per_message: 20
  max_users_per_reaction_page: 100

emojis:
  max_emojis_per_server: 50
  max_animated_emojis_per_server: 50
  max_emoji_size: 262144  # 256KB
  emoji_allowed_formats:
    - image/png
    - image/gif
    - image/webp
  emoji_max_name_length: 32
  emoji_min_name_length: 2
```

## Permission Integration

For server channels, the module checks:

| Permission | Description |
|------------|-------------|
| messages.add_reactions | Required to add reactions |
| messages.manage | Required for moderator actions |
| server.manage | Required to create/delete custom emoji |

## Blocked User Behavior

- Blocked users cannot see each other's reactions
- Reaction counts exclude blocked users
- User lists exclude blocked users

## Error Handling

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
    # Custom emoji errors
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

# Custom emoji error handling
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

## Database Schema

Tables (prefixed with `react_`):
- `react_reactions` - User reactions on messages
- `react_custom_emoji` - Server custom emoji definitions

### Custom Emoji Table Schema

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER | Snowflake ID (primary key) |
| server_id | INTEGER | Server the emoji belongs to |
| name | TEXT | Emoji name (unique per server) |
| animated | INTEGER | 1 if animated, 0 if static |
| url | TEXT | URL to emoji image |
| size | INTEGER | File size in bytes |
| width | INTEGER | Image width (nullable) |
| height | INTEGER | Image height (nullable) |
| created_by | INTEGER | User ID who created the emoji |
| available | INTEGER | 1 if available for use |
| created_at | INTEGER | Creation timestamp |

## Testing

```bash
pytest src/tests/reactions/ -v
```
