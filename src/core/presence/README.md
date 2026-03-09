# Presence Module

User presence and status management system for Plexichat API supporting online status, custom status messages, activities, and typing indicators.

## Features

- User status: online, idle, dnd (do not disturb), invisible, offline
- Custom status message with optional emoji and expiration
- Activity types: playing, streaming, listening, watching, competing, custom
- Activity data (game name, stream URL, song info, timestamps, assets)
- Last seen timestamp (updated on activity)
- Typing indicators with automatic 10-second timeout
- Get user presence (single and bulk)
- Get online friends (integrates with relationships)
- Get online members in server (integrates with servers)
- Presence auto-expires to offline after configurable timeout
- Invisible users appear offline to others but can see others
- Blocked users cannot see real presence

## Setup

```python
from src.core.database import Database
from src.core import auth
from src.core import relationships
from src.core import servers
from src.core import presence

# Initialize database
db = Database()
db.connect()

# Initialize dependencies
auth.setup(db)
relationships.setup(db, auth)
servers.setup(db, auth)

# Initialize presence
presence.setup(db, auth, relationships, servers)
```

## Usage

### Status Management

```python
from src.core import presence

# Set user status
presence.set_status(user_id, presence.UserStatus.ONLINE)
presence.set_status(user_id, presence.UserStatus.IDLE)
presence.set_status(user_id, presence.UserStatus.DND)
presence.set_status(user_id, presence.UserStatus.INVISIBLE)

# Get user status
status = presence.get_status(user_id)

# Clear status (set to offline)
presence.clear_status(user_id)
```

### Custom Status

```python
# Set custom status
presence.set_custom_status(
    user_id=user_id,
    text="Working on a project",
    emoji=":computer:",
    expires_at=1700000000000  # Optional expiration timestamp
)

# Get custom status
custom = presence.get_custom_status(user_id)
if custom:
    print(f"{custom.emoji} {custom.text}")

# Clear custom status
presence.clear_custom_status(user_id)
```

### Activities

```python
# Set playing activity
presence.set_activity(
    user_id=user_id,
    activity_type=presence.ActivityType.PLAYING,
    name="Minecraft",
    details="Building a castle",
    state="In Creative Mode",
    timestamps={"start": 1700000000000}
)

# Set streaming activity
presence.set_activity(
    user_id=user_id,
    activity_type=presence.ActivityType.STREAMING,
    name="Live Coding Session",
    url="https://twitch.tv/example"
)

# Set listening activity
presence.set_activity(
    user_id=user_id,
    activity_type=presence.ActivityType.LISTENING,
    name="Spotify",
    details="Song Title",
    state="by Artist Name"
)

# Get activity
activity = presence.get_activity(user_id)

# Clear activity
presence.clear_activity(user_id)
```

### Full Presence

```python
# Get full presence (status + custom status + activity)
pres = presence.get_presence(user_id)
print(f"Status: {pres.status.value}")
print(f"Last seen: {pres.last_seen}")

# Get multiple presences
presences = presence.get_presences([user1_id, user2_id, user3_id])

# Update last seen
presence.update_last_seen(user_id)
```

### Typing Indicators

```python
# Start typing (auto-expires after 10 seconds)
indicator = presence.start_typing(user_id, channel_id)

# Stop typing manually
presence.stop_typing(user_id, channel_id)

# Get users typing in a channel
typing_users = presence.get_typing_users(channel_id)
for indicator in typing_users:
    print(f"User {indicator.user_id} is typing...")
```

### Online Queries

```python
# Get online friends
online_friends = presence.get_online_friends(user_id)

# Get online server members
online_members = presence.get_online_server_members(user_id, server_id)
```

### Visibility Rules

```python
# Get presence as visible to a specific viewer
# (respects invisible mode and blocks)
visible_presence = presence.get_visible_presence(viewer_id, target_id)

# Check if viewer can see target's real presence
can_see = presence.can_see_presence(viewer_id, target_id)
```

## Status Types

| Status | Description |
|--------|-------------|
| online | User is online and active |
| idle | User is online but inactive |
| dnd | Do not disturb - no notifications |
| invisible | Appears offline to others |
| offline | User is offline |

## Activity Types

| Type | Description |
|------|-------------|
| playing | Playing a game |
| streaming | Streaming content |
| listening | Listening to music |
| watching | Watching content |
| competing | Competing in an activity |
| custom | Custom activity |

## Visibility Rules

1. Users always see their own real presence
2. Blocked users see target as offline
3. Invisible users appear offline to others
4. Invisible users can still see others' presence

## Real-Time WebSocket Updates

Presence changes are automatically broadcast via WebSocket to:

- **Friends** - All users in the friend list
- **Server members** - All users in shared servers

### Events Triggered

| Action | Event | Recipients |
|--------|-------|------------|
| User connects | PRESENCE_UPDATE (online) | Friends + server members |
| Status change | PRESENCE_UPDATE | Friends + server members |
| User disconnects | PRESENCE_UPDATE (offline) | Friends + server members |

### Event Payload

```json
{
    "user_id": "123456789",
    "status": "online",
    "custom_status": "Working on a project",
    "custom_emoji": ":computer:"
}
```

### Client Integration

```javascript
// Handle presence update from WebSocket
function handlePresenceUpdate(data) {
    // Update relationships array
    const rel = AppState.relationships.find(r => r.user_id === data.user_id);
    if (rel) {
        rel.presence = { status: data.status, custom_status: data.custom_status };
    }
    
    // Update UI elements
    updateFriendPresence(data.user_id, data.status);
}
```

## Configuration

Settings in `config/config.yaml` under `presence`:

```yaml
presence:
  typing_timeout_ms: 10000  # 10 seconds
  timeout_ms: 300000  # 5 minutes for auto-offline
```

## Error Handling

All presence errors inherit from `PresenceError`:

```python
from src.core.presence import (
    PresenceError,
    UserNotFoundError,
    InvalidStatusError,
    InvalidActivityError,
    TypingIndicatorError,
    PresenceNotFoundError,
)

try:
    presence.set_status(user_id, status)
except UserNotFoundError:
    print("User not found")
except InvalidStatusError:
    print("Invalid status value")
```

## Database Schema

Tables (prefixed with `pres_`):
- `pres_presence` - User status and last seen
- `pres_custom_status` - Custom status messages
- `pres_activity` - User activities
- `pres_typing` - Typing indicators

## Testing

```bash
pytest src/tests/presence/ -v
```
