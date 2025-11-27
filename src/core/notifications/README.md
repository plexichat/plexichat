# Notifications Module

Mentions and notifications system for PlexiChat API supporting user mentions, role mentions, @everyone/@here, channel references, and notification preferences.

## Features

- Mention types:
  - @user (format: `<@user_id>`)
  - @role (format: `<@&role_id>`)
  - @everyone (requires permission)
  - @here (online members only, requires permission)
  - #channel (format: `<#channel_id>`)
- Parse mentions from message content
- Validate mentions (user exists, role exists, has permission)
- Notification preferences per user:
  - All messages / Only mentions / Nothing (per server)
  - DM notifications on/off
  - Suppress @everyone/@here
  - Mobile push on/off
- Notification preferences per channel (override server):
  - Muted (no notifications)
  - Only @mentions
  - All messages
- Get unread counts with mention counts
- Mark notifications as read
- Get notification feed (recent mentions across all servers)
- Highlight mentions in message (return mention positions)
- Push notification hooks (prepare payload, don't send)

## Setup

```python
from src.core.database import Database
from src.core import auth
from src.core import messaging
from src.core import servers
from src.core import relationships
from src.core import presence
from src.core import notifications

# Initialize database
db = Database()
db.connect()

# Initialize dependencies
auth.setup(db)
messaging.setup(db, auth)
servers.setup(db, auth, messaging)
relationships.setup(db, auth, servers)
presence.setup(db, auth, relationships, servers)

# Initialize notifications
notifications.setup(db, messaging, servers, relationships, presence)
```

## Usage

### Parse Mentions

```python
from src.core import notifications

content = "Hey <@123> check out <#456> with @everyone"
mentions = notifications.parse_mentions(content)

for mention in mentions:
    print(f"Type: {mention.mention_type.value}")
    print(f"Target: {mention.target_id}")
    print(f"Position: {mention.start_pos}-{mention.end_pos}")
```

### Validate Mentions

```python
# Validate mentions before sending
validated = notifications.validate_mentions(
    user_id=sender_id,
    mentions=mentions,
    server_id=server_id,
    channel_id=channel_id
)

for mention in validated:
    if not mention.valid:
        print(f"Invalid: {mention.error}")
```

### Create Notifications

```python
# Create notifications for all mentioned users
notifs = notifications.create_notifications_for_message(
    sender_id=user_id,
    message_id=msg.id,
    conversation_id=conv_id,
    content="Hey <@456> check this out!",
    server_id=server_id,
    channel_id=channel_id
)
```

### Get Notifications

```python
# Get user's notifications
notifs = notifications.get_notifications(user_id, limit=50)

# Get unread only
unread = notifications.get_notifications(user_id, unread_only=True)

# Pagination
older = notifications.get_notifications(user_id, before_id=notifs[-1].id)
```

### Mark as Read

```python
# Mark single notification
notifications.mark_notification_read(user_id, notification_id)

# Mark all as read
count = notifications.mark_all_read(user_id)

# Mark channel as read
count = notifications.mark_channel_read(user_id, channel_id)

# Mark server as read
count = notifications.mark_server_read(user_id, server_id)
```

### Unread Counts

```python
# Get total unread
unread = notifications.get_unread_count(user_id)
print(f"Unread: {unread.total_unread}, Mentions: {unread.mention_count}")

# Get per-server unread
unread = notifications.get_unread_count(user_id, server_id=server_id)

# Get all unread counts
counts = notifications.get_unread_counts(user_id)
for conv_id, count in counts.items():
    print(f"Conversation {conv_id}: {count.total_unread} unread")
```

### Notification Feed

```python
# Get recent mentions across all servers
feed = notifications.get_notification_feed(user_id, limit=50)

print(f"Total: {feed.total_count}")
print(f"Unread: {feed.unread_count}")
print(f"Has more: {feed.has_more}")

for notif in feed.notifications:
    print(f"{notif.mention_type.value}: {notif.content_preview}")
```

### Notification Settings

```python
# Get settings (global)
settings = notifications.get_notification_settings(user_id)

# Get settings (per-server)
settings = notifications.get_notification_settings(user_id, server_id)

# Update settings
settings = notifications.update_notification_settings(
    user_id=user_id,
    server_id=server_id,
    level=notifications.NotificationLevel.ONLY_MENTIONS,
    suppress_everyone=True,
    mobile_push=False
)
```

### Channel Overrides

```python
# Set channel override
override = notifications.set_channel_override(
    user_id=user_id,
    channel_id=channel_id,
    level=notifications.NotificationLevel.MUTED,
    muted_until=1700000000000  # Optional expiration
)

# Get channel override
override = notifications.get_channel_override(user_id, channel_id)

# Delete override
notifications.delete_channel_override(user_id, channel_id)
```

### Highlight Mentions

```python
# Get mention positions for highlighting
positions = notifications.highlight_mentions(content, user_id)

for pos in positions:
    print(f"Position {pos.start}-{pos.end}: {pos.mention_type.value}")
    if pos.is_self:
        print("  -> This mentions you!")
```

### Push Notification Hooks

```python
# Prepare push payload (does not send)
payload = notifications.prepare_push_payload(notification)

print(f"Title: {payload.title}")
print(f"Body: {payload.body}")
print(f"Badge: {payload.badge_count}")
print(f"Data: {payload.data}")
```

## Mention Formats

| Type | Format | Example |
|------|--------|---------|
| User | `<@user_id>` | `<@123456789>` |
| Role | `<@&role_id>` | `<@&987654321>` |
| Channel | `<#channel_id>` | `<#456789123>` |
| Everyone | `@everyone` | `@everyone` |
| Here | `@here` | `@here` |

## Notification Levels

| Level | Description |
|-------|-------------|
| all | Receive all message notifications |
| mentions | Only receive mention notifications |
| nothing | No notifications |
| muted | Muted (same as nothing, for channel overrides) |

## Permission Integration

| Permission | Description |
|------------|-------------|
| messages.mention_everyone | Required for @everyone and @here |
| roles.manage | Can mention non-mentionable roles |

## Blocked User Behavior

- Blocked users do not receive notifications from blocker
- Users who blocked sender do not receive notifications
- Mutual blocks prevent all notifications

## Error Handling

All notification errors inherit from `NotificationError`:

```python
from src.core.notifications import (
    NotificationError,
    UserNotFoundError,
    MessageNotFoundError,
    ChannelNotFoundError,
    ServerNotFoundError,
    InvalidMentionError,
    PermissionDeniedError,
    NotificationNotFoundError,
    SettingsNotFoundError,
)

try:
    notifications.mark_notification_read(user_id, notif_id)
except NotificationNotFoundError:
    print("Notification not found")
except PermissionDeniedError as e:
    print(f"Missing permission: {e.permission}")
```

## Database Schema

Tables (prefixed with `notif_`):
- `notif_notifications` - Notification records
- `notif_settings` - User notification settings (global and per-server)
- `notif_channel_overrides` - Per-channel notification overrides
- `notif_unread` - Unread tracking per conversation

## Testing

```bash
pytest src/tests/notifications/ -v
```
