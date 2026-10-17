# Notifications module

This document describes the mentions and notifications system for Plexichat. Use this module to parse mentions, validate them, create notifications, manage notification preferences, and prepare push payloads.

## Overview

The notifications module supports user mentions, role mentions, `@everyone`, `@here`, and channel references. It handles notification preferences at the global, per-server, and per-channel levels, unread tracking, and push payload preparation.

Supported mention types:

*   `@user` in the format `<@user_id>`
*   `@role` in the format `<@&role_id>`
*   `@everyone` — requires permission
*   `@here` — notifies online members only, requires permission
*   `#channel` in the format `<#channel_id>`

## Before you begin

Initialize the module once at startup. You must set up dependencies in order before you call `notifications.setup`.

```python
from src.core.database import Database
from src.core import auth
from src.core import messaging
from src.core import servers
from src.core import relationships
from src.core import presence
from src.core import notifications

db = Database()
db.connect()

auth.setup(db)
messaging.setup(db, auth)
servers.setup(db, auth, messaging)
relationships.setup(db, auth, servers)
presence.setup(db, auth, relationships, servers)

notifications.setup(db, messaging, servers, relationships, presence)
```

The `setup` function is defined at `plexichat/src/core/notifications/__init__.py:101`. Pass `None` for any optional module that you do not use; the manager degrades gracefully.

## Parse mentions

Extract all mentions from message content. See `plexichat/src/core/notifications/parser.py:25`.

```python
from src.core import notifications

content = "Hey <@123> check out <#456> with @everyone"
mentions = notifications.parse_mentions(content)

for mention in mentions:
    print(f"Type: {mention.mention_type.value}")
    print(f"Target: {mention.target_id}")
    print(f"Position: {mention.start_pos}-{mention.end_pos}")
```

## Validate mentions

Validate mentions before you send a message. The validator checks that users and roles exist and that the sender has permission to use `@everyone` and `@here`.

```python
validated = notifications.validate_mentions(
    user_id=sender_id,
    mentions=mentions,
    server_id=server_id,
    channel_id=channel_id,
)

for mention in validated:
    if not mention.valid:
        print(f"Invalid: {mention.error}")
```

## Create notifications

Create notifications for all mentioned users in a message. The manager filters out blocked users and respects notification preferences.

```python
notifs = notifications.create_notifications_for_message(
    message_id=msg.id,
    author_id=user_id,
    conversation_id=conv_id,
    content="Hey <@456> check this out!",
    server_id=server_id,
    channel_id=channel_id,
)
```

Function signature is at `plexichat/src/core/notifications/__init__.py:170`.

## Get notifications

```python
# Get recent notifications
notifs = notifications.get_notifications(user_id, limit=50)

# Get only unread notifications
unread = notifications.get_notifications(user_id, unread_only=True)

# Paginate with a cursor
older = notifications.get_notifications(user_id, before_id=notifs[-1].id)
```

## Mark notifications as read

```python
# Mark a single notification as read
notifications.mark_notification_read(user_id, notification_id)

# Mark all notifications as read
count = notifications.mark_all_read(user_id)

# Mark all notifications in a channel as read
count = notifications.mark_channel_read(user_id, channel_id)

# Mark all notifications in a server as read
count = notifications.mark_server_read(user_id, server_id)
```

## Get unread counts

```python
# Get total unread count
unread = notifications.get_unread_count(user_id)
print(f"Unread: {unread.unread_count}, Mentions: {unread.mention_count}")

# Get unread count for a specific server
unread = notifications.get_unread_count(user_id, server_id=server_id)

# Get unread counts for all conversations
counts = notifications.get_unread_counts(user_id)
for conv_id, count in counts.items():
    print(f"Conversation {conv_id}: {count.unread_count} unread")
```

## Get the notification feed

Get recent mentions across all servers for a notification feed.

```python
feed = notifications.get_notification_feed(user_id, limit=50)

print(f"Total: {feed.total_count}")
print(f"Unread: {feed.unread_count}")
print(f"Has more: {feed.has_more}")

for notif in feed.notifications:
    print(f"{notif.mention_type.value}: {notif.content_preview}")
```

## Manage notification settings

Settings exist at two levels: global and per-server. Per-server settings override the global level.

```python
# Get global settings
settings = notifications.get_notification_settings(user_id)

# Get per-server settings
settings = notifications.get_notification_settings(user_id, server_id)

# Update settings
settings = notifications.update_notification_settings(
    user_id=user_id,
    server_id=server_id,
    level=notifications.NotificationLevel.ONLY_MENTIONS,
    suppress_everyone=True,
    mobile_push=False,
)
```

Available fields on `NotificationSettings` at `plexichat/src/core/notifications/models.py:83`:

| Field | Type | Description |
|-------|------|-------------|
| `level` | `NotificationLevel` | `ALL_MESSAGES`, `ONLY_MENTIONS`, or `NOTHING`. |
| `dm_notifications` | `bool` | Whether to receive direct-message notifications. |
| `suppress_everyone` | `bool` | Whether to suppress `@everyone` and `@here`. |
| `suppress_roles` | `bool` | Whether to suppress role mentions. |
| `mobile_push` | `bool` | Whether to send mobile push notifications. |

## Manage channel overrides

Channel overrides take precedence over server settings.

```python
# Set a channel override
override = notifications.set_channel_override(
    user_id=user_id,
    channel_id=channel_id,
    level=notifications.NotificationLevel.MUTED,
    muted_until=1700000000000,  # Optional expiration timestamp
)

# Get a channel override
override = notifications.get_channel_override(user_id, channel_id)

# Delete a channel override
notifications.delete_channel_override(user_id, channel_id)
```

## Highlight mentions

Get the positions of mentions in content for rendering highlights. The `is_self` flag indicates whether the mention targets the requesting user.

```python
positions = notifications.highlight_mentions(content, user_id)

for pos in positions:
    print(f"Position {pos.start_pos}-{pos.end_pos}: {pos.mention_type.value}")
    if pos.is_self:
        print("  -> This mentions you!")
```

## Prepare push payloads

Prepare a push notification payload without sending it. The caller is responsible for delivery.

```python
payload = notifications.prepare_push_payload(notification)

print(f"Title: {payload.title}")
print(f"Body: {payload.body}")
print(f"Badge: {payload.badge_count}")
print(f"Data: {payload.data}")
```

## Mention formats

| Type | Format | Example |
|------|--------|---------|
| User | `<@user_id>` | `<@123456789>` |
| Role | `<@&role_id>` | `<@&987654321>` |
| Channel | `<#channel_id>` | `<#456789123>` |
| Everyone | `@everyone` | `@everyone` |
| Here | `@here` | `@here` |

Patterns are defined at `plexichat/src/core/notifications/parser.py:18`.

## Notification levels

| Level | Value | Description |
|-------|-------|-------------|
| All messages | `all` | Receive all message notifications. |
| Only mentions | `mentions` | Receive only mention notifications. |
| Nothing | `nothing` | Receive no notifications. |
| Muted | `muted` | Muted. Same as `nothing` for channel overrides. |

Defined at `plexichat/src/core/notifications/models.py:32`.

## Permissions

| Permission | Description |
|------------|-------------|
| `messages.mention_everyone` | Required to use `@everyone` and `@here`. |
| `roles.manage` | Required to mention non-mentionable roles. |

If the sender lacks permission, the mention is marked invalid during validation and no notification is created.

## Blocked user behavior

*   Blocked users do not receive notifications from the blocker.
*   If you block the sender, you do not receive notifications from them.
*   Mutual blocks prevent all notifications between the two users.

The manager checks the relationships module before creating notifications. See `plexichat/src/core/notifications/manager/creator.py`.

## Error handling

All errors inherit from `NotificationError`. Import them from `src.core.notifications`.

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

Exceptions are defined at `plexichat/src/core/notifications/exceptions.py`.

## Database schema

Tables are prefixed with `notif_`. Schema is defined at `plexichat/src/core/notifications/schema.py:10`.

| Table | Description |
|-------|-------------|
| `notif_notifications` | Notification records with `user_id`, `message_id`, and `mention_type`. |
| `notif_settings` | User notification settings, global and per-server. Unique on `(user_id, server_id)`. |
| `notif_channel_overrides` | Per-channel overrides. Unique on `(user_id, channel_id)`. |
| `notif_unread` | Unread tracking per conversation. Unique on `(user_id, conversation_id)`. |

## Test

Run the notification tests:

```bash
pytest src/tests/notifications/ -v
```

## Related code

*   `plexichat/src/core/notifications/__init__.py` — public API
*   `plexichat/src/core/notifications/parser.py` — mention parsing
*   `plexichat/src/core/notifications/models.py` — data classes and enums
*   `plexichat/src/core/notifications/manager/` — creation, validation, and feed logic
*   `plexichat/src/core/notifications/schema.py` — table definitions
