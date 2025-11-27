# Reactions Module

Message reaction system for PlexiChat API supporting Unicode emoji and custom server emoji with permission checks and block filtering.

## Features

- Add/remove reactions to messages
- Unicode emoji support
- Custom emoji support (server-specific, format: `<:name:id>`)
- Reaction counts per emoji
- User lists for each reaction (paginated)
- One reaction per emoji per user
- Configurable max unique reactions per message (default 20)
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
from src.core import reactions

# Initialize database
db = Database()
db.connect()

# Initialize dependencies
auth.setup(db)
messaging.setup(db, auth)
servers.setup(db, auth, messaging)
relationships.setup(db, auth, servers)

# Initialize reactions
reactions.setup(db, messaging, servers, relationships)
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
# Create custom emoji for server
emoji = reactions.create_custom_emoji(
    user_id=owner_id,
    server_id=server_id,
    name="pepe",
    animated=False
)

# Get server's custom emojis
emojis = reactions.get_server_custom_emojis(server_id)

# Delete custom emoji
reactions.delete_custom_emoji(owner_id, emoji.id)
```

## Configuration

Settings in `config/config.yaml` under `reactions`:

```yaml
reactions:
  max_reactions_per_message: 20
  max_users_per_reaction_page: 100
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
```

## Database Schema

Tables (prefixed with `react_`):
- `react_reactions` - User reactions on messages
- `react_custom_emoji` - Server custom emoji definitions

## Testing

```bash
pytest src/tests/reactions/ -v
```
