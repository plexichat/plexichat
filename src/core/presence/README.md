# Presence

Manage user presence, custom status, activities, and typing indicators.

## Overview

The presence module tracks online status, custom messages, activities, and typing state. It integrates with relationships and servers for visibility rules and online-member queries, and auto-expires stale presence.

Features:

- Status values: `online`, `idle`, `dnd`, `invisible`, `offline`.
- Custom status with optional emoji and expiration.
- Activities: `playing`, `streaming`, `listening`, `watching`, `competing`, `custom`.
- Last-seen timestamp updated on activity.
- Typing indicators with automatic timeout.
- Bulk presence queries for friends and server members.
- Visibility rules for invisible mode and blocked users.

## Before you begin

Initialize the module once at startup.

```python
from src.core.database import Database
from src.core import auth, relationships, servers, presence

db = Database()
db.connect()

auth.setup(db)
relationships.setup(db, auth)
servers.setup(db, auth)
presence.setup(db, auth, relationships, servers)
```

## Manage status

```python
from src.core import presence

presence.set_status(user_id, presence.UserStatus.ONLINE)
presence.set_status(user_id, presence.UserStatus.IDLE)
presence.set_status(user_id, presence.UserStatus.DND)
presence.set_status(user_id, presence.UserStatus.INVISIBLE)

status = presence.get_status(user_id)
presence.clear_status(user_id)  # Sets status to offline
```

| Status | Behavior |
|--------|----------|
| `online` | User is active. |
| `idle` | User is online but inactive. |
| `dnd` | Do not disturb; suppresses notifications. |
| `invisible` | Appears as `offline` to other users. |
| `offline` | User is not connected. |

On startup, call `presence.sweep_all_offline()` once to clear stale live statuses left by a crash. The sweep is guarded by a distributed lock so only one worker runs it.

## Manage custom status

```python
presence.set_custom_status(
    user_id=user_id,
    text="Working on a project",
    emoji=":computer:",
    expires_at=1700000000000,
)

custom = presence.get_custom_status(user_id)
if custom:
    print(f"{custom.emoji} {custom.text}")

presence.clear_custom_status(user_id)
```

Expired custom statuses are removed automatically on read.

## Manage activities

```python
presence.set_activity(
    user_id=user_id,
    activity_type=presence.ActivityType.PLAYING,
    name="Minecraft",
    details="Building a castle",
    state="In Creative Mode",
    timestamps={"start": 1700000000000},
)

presence.set_activity(
    user_id=user_id,
    activity_type=presence.ActivityType.STREAMING,
    name="Live Coding Session",
    url="https://twitch.tv/example",
)

activity = presence.get_activity(user_id)
presence.clear_activity(user_id)
```

| Type | Description |
|------|-------------|
| `playing` | Playing a game. |
| `streaming` | Streaming content. |
| `listening` | Listening to music. |
| `watching` | Watching content. |
| `competing` | Competing in an event. |
| `custom` | Custom activity. |

## Get presence

```python
# Full presence for one user
pres = presence.get_presence(user_id)
print(f"Status: {pres.status.value}")
print(f"Last seen: {pres.last_seen}")

# Multiple users
presences = presence.get_presences([user1_id, user2_id, user3_id])

# Update last-seen timestamp
presence.update_last_seen(user_id)

# Track focused channel (transient, Redis only)
presence.set_focused_channel(user_id, channel_id=10, server_id=1)
```

## Use typing indicators

Typing indicators expire automatically after the configured timeout.

```python
presence.start_typing(user_id, channel_id)

presence.stop_typing(user_id, channel_id)

typing_users = presence.get_typing_users(channel_id)
for indicator in typing_users:
    print(f"User {indicator.user_id} is typing...")

# Clear all channels for a user
presence.clear_all_typing(user_id)
```

## Query online users

```python
online_friends = presence.get_online_friends(user_id)
online_members = presence.get_online_server_members(user_id, server_id)
```

## Check visibility

Visibility respects blocks and invisible mode.

```python
# Presence as seen by a specific viewer
visible = presence.get_visible_presence(viewer_id, target_id)

# Bulk variant
visible_map = presence.get_visible_presences_bulk(viewer_id, [target1, target2])

# Whether the viewer can see the target's real status
can_see = presence.can_see_presence(viewer_id, target_id)
```

Visibility rules:

1. You always see your own real presence.
2. A user you block, or who blocks you, appears as `offline`.
3. A user with `invisible` status appears as `offline` to others. Their custom status, activity, and timestamps are not revealed.
4. An `invisible` user can still see other users' presence.

## Real-time updates

Presence changes are broadcast over WebSocket to friends and server members.

| Action | Event | Recipients |
|--------|-------|------------|
| User connects | `PRESENCE_UPDATE` (`online`) | Friends and server members |
| Status changes | `PRESENCE_UPDATE` | Friends and server members |
| User disconnects | `PRESENCE_UPDATE` (`offline`) | Friends and server members |

Event payload:

```json
{
  "user_id": "123456789",
  "status": "online",
  "custom_status": "Working on a project",
  "custom_emoji": ":computer:"
}
```

Client example:

```javascript
function handlePresenceUpdate(data) {
  const rel = AppState.relationships.find(r => r.user_id === data.user_id);
  if (rel) {
    rel.presence = { status: data.status, custom_status: data.custom_status };
  }
  updateFriendPresence(data.user_id, data.status);
}
```

## Configuration

Configure `config/config.yaml` under `presence`:

```yaml
presence:
  typing_timeout_ms: 10000  # Typing indicator timeout
  timeout_ms: 300000        # Auto-offline timeout (5 minutes)
```

## Error handling

All presence errors inherit from `PresenceError`:

```python
from src.core.presence import (
    PresenceError,
    UserNotFoundError,
    InvalidStatusError,
    InvalidActivityError,
)

try:
    presence.set_status(user_id, status)
except UserNotFoundError:
    print("User not found.")
except InvalidStatusError:
    print("Invalid status value.")
```

## Database schema

| Table | Description |
|-------|-------------|
| `pres_presence` | Status and `last_seen` per user. |
| `pres_custom_status` | Custom status text, emoji, and expiry. |
| `pres_activity` | Current activity per user. |
| `pres_typing` | Typing indicators (`UNIQUE(user_id, channel_id)`). |

## Testing

```bash
pytest src/tests/presence/ -v
```
