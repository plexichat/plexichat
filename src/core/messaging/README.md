# Messaging

Use the messaging module to send direct messages and group messages in Plexichat with rich text, attachments, delivery receipts, and content filtering.

## Overview

The module provides:

- Direct messages (DMs) with auto-create and server-channel conversations
- Group conversations with `owner`, `admin`, and `member` roles
- Snowflake IDs for globally unique, sortable identifiers
- Rich text formatting (bold, italic, spoiler, code, quote, strikethrough)
- Content filtering for profanity, NSFW, and custom blocked words
- Attachments with per-user and global size limits
- Delivery and read receipts
- Message pinning and soft delete with audit trail
- At-rest encryption for messages and attachments
- Event bus for real-time delivery to WebSockets and other subscribers

## Architecture

The module uses a layered architecture:

```
+-------------------------------------------------------------+
|                    MessagingManager                          |
|                    (facade)                                  |
+-------------------------------------------------------------+
|  ConversationService | MessageService | ParticipantService    |
|  MessageStatusService | AttachmentService | PinService        |
|  UserSettingsService | ContentFilterService                  |
+-------------------------------------------------------------+
|  ConversationRepository | MessageRepository | ParticipantRepo |
|  MessageStatusRepository| AttachmentRepository| PinRepository |
|  UserSettingsRepository                                      |
+-------------------------------------------------------------+
|                    Database (SQLite / PostgreSQL)            |
+-------------------------------------------------------------+
```

Design principles:

- **Single responsibility** — Each service manages one domain.
- **Repository pattern** — Repositories abstract data access for database portability.
- **Batch operations** — Batch fetch methods avoid N+1 queries.
- **Event bus** — The event bus delivers events reliably with optional retry.
- **Caching** — In-memory cache with TTL for frequently accessed data.

### Module structure

```
messaging/
├── __init__.py          # Public API (setup, create_dm, send_message, ...)
├── manager.py           # MessagingManager facade
├── models.py            # Dataclasses: Conversation, Message, Participant, ...
├── exceptions.py        # Typed errors
├── schema.py            # Database schema
├── content.py           # Content validation
├── events.py            # Event bus
├── repositories/        # Data access layer
└── services/            # Business logic layer
```

## Prerequisites

This module depends on the database and auth modules. The required packages are already installed with the auth module:

```bash
pip install argon2-cffi cryptography PyYAML
```

## Setup

Initialize the database, call `auth.setup()`, and then call `messaging.setup()`. Call `setup()` once at application startup.

```python
from src.core.database import Database
from src.core import auth
from src.core import messaging

db = Database()
db.connect()

auth.setup(db)
messaging.setup(db)
```

Use `messaging.get_manager()` (`__init__.py:97`) only after you call `setup()`.

## Create and manage conversations

### Direct messages

```python
from src.core import messaging

# Create or get the existing DM between two users
dm = messaging.create_dm(user_id=1, recipient_id=2)

msg = messaging.send_message(
    user_id=1,
    conversation_id=dm.id,
    content="Hello! How are you?",
)

messages = messaging.get_messages(user_id=1, conversation_id=dm.id, limit=50)
```

`create_dm()` (`__init__.py:104`) respects `dm_auto_create` and per-user `auto_create_dms` settings.

### Group conversations

```python
group = messaging.create_group(
    owner_id=1,
    name="Project Team",
    participant_ids=[2, 3, 4],
)

messaging.add_participant(
    user_id=1,
    conversation_id=group.id,
    participant_id=5,
    role=messaging.ParticipantRole.MEMBER,
)

messaging.update_participant_role(
    user_id=1,
    conversation_id=group.id,
    participant_id=5,
    role=messaging.ParticipantRole.ADMIN,
)

messaging.remove_participant(user_id=1, conversation_id=group.id, participant_id=5)
```

### Server channels and threads

```python
channel_conv = messaging.create_server_channel_conversation(
    server_id=10, channel_id=20
)
thread_conv = messaging.create_thread_conversation(
    server_id=10, channel_id=20, name="Bug discussion"
)
```

### List and manage conversations

```python
conversations = messaging.get_conversations(user_id=1, limit=50, before_id=cursor)
conversation = messaging.get_conversation(conversation_id=group.id, user_id=1)

messaging.update_conversation(user_id=1, conversation_id=group.id, name="New name")
messaging.leave_conversation(user_id=2, conversation_id=group.id)
messaging.delete_conversation(user_id=1, conversation_id=group.id)
```

### Participants and muting

```python
participants = messaging.get_participants(user_id=1, conversation_id=group.id)
ids = messaging.get_participant_ids(group.id)

if messaging.is_participant(conversation_id=group.id, user_id=2):
    print("User is a participant")

messaging.mute_conversation(user_id=1, conversation_id=group.id, muted=True)
```

## Send and manage messages

### Send a message

```python
msg = messaging.send_message(
    user_id=1,
    conversation_id=group.id,
    content="Hello **world**",
    reply_to_id=original_msg.id,
    attachments=[{
        "filename": "doc.pdf",
        "content_type": "application/pdf",
        "size": 1024000,
        "url": "https://storage.example.com/files/doc.pdf",
    }],
)
```

### Rich text formatting

Include markdown-style formatting in `content`:

| Style | Syntax | Example |
|---|---|---|
| Bold | `**text**` | `**bold**` |
| Italic | `*text*` | `*italic*` |
| Spoiler | `\|\|text\|\|` | `\|\|secret\|\|` |
| Inline code | `` `code` `` | `` `print()` `` |
| Code block | ` ```lang ... ``` ` | ` ```python ... ``` ` |
| Strikethrough | `~~text~~` | `~~wrong~~` |
| Quote | `> text` | `> A quote` |

```python
messaging.send_message(user_id, conv_id, "This is **bold** and ||spoiler||")
messaging.send_message(user_id, conv_id, "Use `print()` for output")
```

### Edit and delete

```python
# Edit your own message
msg = messaging.edit_message(user_id=1, message_id=msg.id, content="Updated")

# Edit with optimistic concurrency
msg = messaging.edit_message(
    user_id=1, message_id=msg.id, content="Updated",
    expected_edited_at=msg.edited_at,
)

# Soft delete (audit trail retained)
messaging.delete_message(user_id=1, message_id=msg.id)

# Hard delete (admin only)
messaging.delete_message(user_id=1, message_id=msg.id, hard_delete=True)

# Bulk delete in a channel
deleted_ids = messaging.delete_messages_bulk(user_id=1, channel_id=20, message_ids=[1, 2, 3])

# System and AutoMod deletion
messaging.send_system_message(group.id, "User joined", event_type="member_add")
messaging.automod_remove_message(msg.id, reason="Blocked word")
```

### Pin messages

```python
messaging.pin_message(user_id=1, message_id=msg.id)
messaging.unpin_message(user_id=1, message_id=msg.id)
pinned = messaging.get_pinned_messages(user_id=1, conversation_id=group.id)
```

### Pagination

Messages use cursor-based pagination with Snowflake IDs:

```python
# First page (newest first)
messages = messaging.get_messages(user_id=1, conversation_id=conv_id, limit=50)

# Older messages
older = messaging.get_messages(
    user_id=1, conversation_id=conv_id, limit=50, before_id=messages[-1].id
)

# Newer messages
newer = messaging.get_messages(
    user_id=1, conversation_id=conv_id, limit=50, after_id=messages[0].id
)
```

### Search

```python
results = messaging.search_messages(
    user_id=1, conversation_id=conv_id, query="hello", limit=50
)
```

## Manage attachments

```python
msg = messaging.send_message(
    user_id=1, conversation_id=conv_id, content="See file",
    attachments=[{"filename": "doc.pdf", "content_type": "application/pdf", "size": 1024, "url": url}],
)

attachments = messaging.get_attachments(user_id=1, message_id=msg.id)
messaging.delete_attachment(user_id=1, attachment_id=attachments[0].id)

# Add an attachment to an existing message
messaging.add_attachment(
    user_id=1, message_id=msg.id,
    filename="extra.png", content_type="image/png", size=2048, url=url,
)
```

Attachment limits are enforced from config (`max_attachment_size`, `max_attachments_per_message`) and per-user settings.

## Track delivery and read status

```python
# Mark as delivered
messaging.mark_delivered(user_id=2, message_ids=[msg1.id, msg2.id])

# Mark as read up to a message
messaging.mark_read(user_id=2, conversation_id=conv_id, up_to_message_id=msg.id)

# Get unread counts
unread = messaging.get_unread_count(user_id=2)
# Returns {conversation_id: count}

unread_one = messaging.get_unread_count(user_id=2, conversation_id=conv_id)

# Last read position
pos = messaging.get_last_read(user_id=2, conversation_id=conv_id)
# Returns {last_read_message_id, last_read_at}

# Mark as unread and mark all channels in a server as read
messaging.mark_unread(user_id=2, conversation_id=conv_id)
messaging.mark_all_server_read(user_id=2, server_id=10)

# Delivery status (sender only)
status = messaging.get_message_status(user_id=1, message_id=msg.id)
for s in status:
    print(f"User {s.user_id}: {s.status.value} at {s.timestamp}")

reader_ids = messaging.get_reader_ids(user_id=1, message_id=msg.id)
batch = messaging.get_batch_reader_ids(user_id=1, message_ids=[msg1.id, msg2.id])
```

## Configure content filtering

```python
filters = messaging.get_user_filter_settings(user_id=1)

messaging.update_user_filter_settings(
    user_id=1,
    profanity_filter=True,
    nsfw_filter=True,
    spoiler_click_to_reveal=True,
    custom_blocked_words=["spam", "advertisement"],
)
```

## Configure user message settings

```python
settings = messaging.get_user_message_settings(user_id=1)

messaging.update_user_message_settings(
    user_id=1,
    allow_dms_from="everyone",  # "everyone", "friends", or "none"
    auto_create_dms=True,
    max_message_length=8000,
    max_attachment_size=20971520,
    max_attachments_per_message=5,
    read_receipts_enabled=True,
    typing_indicators_enabled=True,
)
```

## Configuration

Set values in `config/config.yaml` under `messaging`:

```yaml
messaging:
  max_message_length: 4000
  max_group_participants: 100
  message_preview_length: 100
  max_attachment_size: 10485760  # 10 MB
  max_attachments_per_message: 10
  dm_auto_create: true
  encrypt_messages: true
  encrypt_attachments: true
  content:
    profanity_words: []
    nsfw_patterns: []
    default_filter_action: censor  # censor, block, warn, or spoiler
```

## Permissions

The module integrates with the auth permission system:

| Permission | Description |
|---|---|
| `messages.send` | Send messages |
| `messages.read` | Read messages |
| `messages.edit` | Edit your own messages |
| `messages.delete` | Delete your own messages |
| `messages.delete_others` | Delete others' messages (moderator) |
| `messages.pin` | Pin messages |
| `messages.react` | Add reactions |
| `conversations.create` | Create conversations |
| `conversations.join` | Join conversations |
| `conversations.leave` | Leave conversations |
| `conversations.invite` | Invite others |
| `conversations.kick` | Remove participants |
| `conversations.manage` | Manage conversation settings |
| `conversations.delete` | Delete conversations |

### Participant roles

| Role | Capabilities |
|---|---|
| `owner` | Full control, delete the conversation, manage all participants |
| `admin` | Add and remove members, manage settings |
| `member` | Send messages, leave the conversation |

## Event bus

Subscribe to messaging events for real-time delivery:

```python
from src.core.messaging.events import get_event_bus, MessagingEventType

bus = get_event_bus()

def handle_event(event, recipients):
    print(f"{event.event_type} for {len(recipients)} users")

bus.subscribe(handle_event)

async def async_handler(event, recipients):
    await notify_websockets(event, recipients)

bus.subscribe_async(async_handler)

# Publish with retry
import asyncio

async def send_with_retry():
    event = bus.create_event(
        MessagingEventType.MESSAGE_CREATE,
        data={"message_id": 123},
        conversation_id=456,
    )
    result = await bus.publish_with_retry(event, [1, 2, 3])
    print(f"Delivered to {result.recipients_count} users")
```

### Event types

`MESSAGE_CREATE`, `MESSAGE_UPDATE`, `MESSAGE_DELETE`, `MESSAGE_ACK`, `MESSAGE_PIN`, `MESSAGE_UNPIN`, `TYPING_START`, `CONVERSATION_CREATE`, `CONVERSATION_UPDATE`, `CONVERSATION_DELETE`, `PARTICIPANT_ADD`, `PARTICIPANT_REMOVE`, `PARTICIPANT_UPDATE`.

## Error handling

All errors inherit from `MessagingError` (`exceptions.py`):

```python
from src.core.messaging import (
    MessagingError,
    ConversationNotFoundError,
    ConversationAccessDeniedError,
    MessageNotFoundError,
    MessageAccessDeniedError,
    ParticipantNotFoundError,
    ParticipantExistsError,
    ParticipantLimitError,
    InvalidContentError,
    ContentTooLongError,
    AttachmentError,
    AttachmentTooLargeError,
    AttachmentLimitError,
)

try:
    messaging.send_message(user_id, conv_id, content)
except ContentTooLongError as e:
    print(f"Too long: {e.actual_length}/{e.max_length}")
except InvalidContentError as e:
    print(f"Invalid: {e.issues}")
except ConversationAccessDeniedError:
    print("You don't have access to this conversation")
```

## Security

- Content validation blocks SQL injection and XSS.
- At-rest encryption uses zero-friction encryption for messages and attachments.
- Snowflake IDs prevent enumeration.
- Soft deletes retain an audit trail.
- Every operation checks permissions and participant membership.

## Database schema

Tables use the `msg_` prefix (`schema.py`):

- `msg_conversations` — conversation metadata
- `msg_participants` — participants and roles
- `msg_messages` — message content
- `msg_message_status` — delivery and read receipts
- `msg_pinned` — pinned messages
- `msg_attachments` — attachments
- `msg_content_filters` — per-user filter settings
- `msg_user_settings` — per-user message settings
- `msg_dm_lookup` — fast DM lookup

## Testing

```bash
pytest src/tests/messaging/ -v
```

## Related code

- `__init__.py:72` — `setup()` and public API
- `manager.py` — `MessagingManager` facade
- `models.py` — data models
- `exceptions.py` — error types
- `services/` — business logic
- `repositories/` — data access
- `events.py` — event bus implementation
