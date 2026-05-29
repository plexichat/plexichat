# Notification Manager

## Purpose
Parses mentions, creates notifications, manages per-user settings, and
dispatches notification events to connected clients.

## Primary Responsibilities
- Parse and validate mentions in message content
- Create notifications and notification feed entries
- Enforce user and channel notification settings
- Dispatch WebSocket notification events
- Maintain unread counts and mention metadata

## Architecture (Mixin-based)
The `NotificationManager` class is composed via mixins:

| File | Mixin | Responsibility |
|------|-------|---------------|
| `manager.py` | `NotificationManager` | Orchestrator, `__init__`, config loading |
| `mention_validator.py` | `MentionValidationMixin` | `parse_mentions`, `validate_mentions`, `highlight_mentions` |
| `creator.py` | `NotificationCreatorMixin` | `create_notifications`, `create_notifications_bulk`, notification filtering |
| `settings.py` | `SettingsMixin` | Settings + override CRUD with Redis caching |
| `unread.py` | `UnreadMixin` | Unread count tracking per conversation |
| `feed.py` | `FeedMixin` | Notification feed, CRUD, mark read operations |
| `push.py` | `PushMixin` | `prepare_push_payload` |
| `event.py` | `EventMixin` | `_dispatch_notification_event` (WebSocket) |
| `helpers.py` | (standalone) | Row converters, DB accessors, blocking utils |

## Core Components
- NotificationManager: notification orchestration and dispatch
- Mention parser and validation helpers
- Notification settings and override models

## Usage

```python
from src.core.notifications.manager import NotificationManager

nm = NotificationManager(db, auth_module=auth, messaging_module=messaging,
                         servers_module=servers, relationships_module=relationships,
                         presence_module=presence)

# Parse mentions from message content
mentions = nm.parse_mentions("Hello @user123, check #general")
# Returns dict with user_ids, role_ids, channel_ids, and whether @everyone/@here was used

# Create notifications
nm.create_notifications(
    message_id=42,
    conversation_id=10,
    server_id=1,
    author_id=2,
    content="Hello @user123",
    mentions=mentions
)

# Get notification feed
feed = nm.get_notifications(user_id=1, limit=50, offset=0)

# Mark as read
nm.mark_read(user_id=1, notification_ids=[10, 11, 12])

# Get unread counts
counts = nm.get_unread_counts(user_id=1)
```

## Error Handling

- Missing message content or invalid message ID raises `ValueError`.
- Permission checks for mention validation (`@here`/`@everyone`) catch exceptions from the servers module and degrade silently.
- Blocked users are filtered out during notification creation (no exception raised).
- WebSocket dispatch failures are caught and logged at debug level — they do not block notification creation.

```python
try:
    nm.create_notifications(message_id=9999, conversation_id=1, author_id=1,
                            content="test", mentions={})
except ValueError as e:
    print(f"Cannot create notification: {e}")
```

## Dependencies
- Messaging module for message lookup.
- Servers module for permission checks (mention validation per role/channel).
- Relationships module for block filtering.
- Presence module for @here targeting.
- WebSocket dispatcher for live events.

## Notes
- Content previews are truncated based on config limits (`content_preview_length`: 100 chars default).
- Mentions are validated for role and channel access.
- An index (`idx_notif_user_created`) is created on initialization for efficient feed queries.
