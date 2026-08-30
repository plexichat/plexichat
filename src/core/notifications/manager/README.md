# Notification manager

The notification manager parses mentions, creates notifications, enforces per-user and per-channel settings, and dispatches events to connected clients.

## Overview

The `NotificationManager` class handles the full notification lifecycle:

- Parses and validates mentions in message content.
- Creates notifications and notification feed entries.
- Enforces user and channel notification settings.
- Dispatches WebSocket notification events.
- Tracks unread counts and mention metadata.

## Architecture

`NotificationManager` is composed from mixins. Each mixin owns one domain.

| File | Mixin | Responsibility |
|------|-------|---------------|
| `manager.py` | `NotificationManager` | Orchestration, `__init__`, and config loading |
| `mention_validator.py` | `MentionValidationMixin` | `parse_mentions`, `validate_mentions`, `highlight_mentions` |
| `creator.py` | `NotificationCreatorMixin` | `create_notifications` and `create_notifications_bulk`, filtering blocked users |
| `settings.py` | `SettingsMixin` | Settings and override CRUD with Redis caching |
| `unread.py` | `UnreadMixin` | Unread counts per conversation |
| `feed.py` | `FeedMixin` | Feed retrieval and mark-read operations |
| `push.py` | `PushMixin` | `prepare_push_payload` |
| `event.py` | `EventMixin` | `_dispatch_notification_event` (WebSocket) |
| `helpers.py` | Standalone helpers | Row conversion, database accessors, and blocking utilities |

The class inherits in this order: `MentionValidationMixin` → `NotificationCreatorMixin` → `SettingsMixin` → `UnreadMixin` → `FeedMixin` → `PushMixin` → `EventMixin` → `BaseManager`.

## Dependencies

You must provide these modules when you construct the manager:

- Messaging module for message lookup.
- Servers module for permission checks on `@here` and `@everyone`.
- Relationships module for block filtering.
- Presence module for `@here` targeting.
- WebSocket dispatcher for live events.

The manager creates the `idx_notif_user_created` index on `(user_id, created_at)` during initialization.

## Configuration

The manager loads configuration from `notifications` in `config.yaml`.

| Key | Default | Description |
|-----|---------|-------------|
| `content_preview_length` | `100` | Maximum characters in `content_preview`. |
| `max_notifications_per_page` | `100` | Maximum items per `get_notifications` call. |
| `max_feed_items` | `100` | Maximum items per `get_notification_feed` call. |

## Usage

### Initialize the manager

```python
from src.core.notifications.manager import NotificationManager

nm = NotificationManager(
    db,
    auth_module=auth,
    messaging_module=messaging,
    servers_module=servers,
    relationships_module=relationships,
    presence_module=presence,
)
```

### Parse mentions

```python
mentions = nm.parse_mentions("Hello @user123, check #general")
# Returns a list of Mention objects with type, target, and position.
```

### Create notifications

```python
nm.create_notifications(
    message_id=42,
    conversation_id=10,
    server_id=1,
    author_id=2,
    content="Hello @user123",
    mentions=mentions,
)
```

### Retrieve and update the feed

```python
# Get the feed
feed = nm.get_notifications(user_id=1, limit=50)

# Mark specific notifications as read
nm.mark_notification_read(user_id=1, notification_id=10)

# Mark all notifications as read
nm.mark_all_read(user_id=1)
```

## Behavior

### Mention validation

The manager validates `@everyone` and `@here` against the servers module. If the servers module is unavailable, validation fails closed unless `notifications.allow_everyone_when_servers_missing` is `true`. DMs always reject `@everyone` and `@here`.

### Feed pagination

`get_notification_feed` returns page-scoped items but computes `total_count` and `unread_count` across the user's full notification set, so badges do not undercount on later pages.

### Preview truncation

Content previews are truncated to `content_preview_length`. When encryption is enabled, the ciphertext is stored separately and the plaintext column is cleared.

## Error handling

| Condition | Result |
|-----------|--------|
| Missing or invalid message content or ID | Raises `ValueError` |
| `@here` or `@everyone` without permission | Marks the mention as invalid; does not raise |
| Author is blocked by recipient | Filters out the notification silently |
| WebSocket dispatch fails | Logs at debug level; does not block creation |

```python
try:
    nm.create_notifications(
        message_id=9999,
        conversation_id=1,
        author_id=1,
        content="test",
        mentions=[],
    )
except ValueError as e:
    print(f"Cannot create notification: {e}")
```
