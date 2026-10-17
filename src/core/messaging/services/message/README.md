# Message Service

Core messaging service that handles sending, editing, deleting, and retrieving messages across conversations. Built as a mixin-based composition assembled via MRO.

## Overview

The message service is the central write/read path for all user-generated and system messages. It enforces access control, content validation, encryption (including per-conversation channel ratchets), attachment handling, search indexing, and notification dispatch. The service is assembled from three mixins into a single `MessageService` class.

## Architecture

```
┌─────────────────────────────────────────────────┐
│                 MessageService                   │
│  ┌──────────────┬───────────────┬─────────────┐ │
│  │  SendMixin   │ EditDeleteMixin│RetrievalMixin│ │
│  └──────┬───────┴───────┬───────┴──────┬──────┘ │
├─────────┴───────────────┴──────────────┴─────────┤
│                   Dependencies                    │
│  ┌─────────────┐ ┌──────────┐ ┌───────────────┐ │
│  │MessageRepo  │ │Attachment│ │ParticipantSvc │ │
│  │PinRepo      │ │PinRepo   │ │UserSettingsSvc│ │
│  │Conversation │ │StatusRepo│ │ContentFilter  │ │
│  │Repo         │ │          │ │ChannelRatchet │ │
│  └─────────────┘ └──────────┘ └───────────────┘ │
└─────────────────────────────────────────────────┘
```

## Key Files

| File | Purpose |
|------|---------|
| `composer.py` | Assembles `MessageService` from mixins via MRO |
| `protocol.py` | `MessageServiceProtocol` defining the shared interface |
| `send.py` | `SendMixin` — message sending, encryption, attachments, notification dispatch |
| `edit_delete.py` | `EditDeleteMixin` — edit, soft/hard delete, bulk delete, archive, AutoMod removal |
| `retrieval.py` | `RetrievalMixin` — get messages, search, recent-message cache with atomic Lua rebuild |
| `search_helpers.py` | Module-level helpers for search indexing (avoids circular imports) |

## Usage

### Send a message

```python
msg = message_service.send_message(
    user_id=user_id,
    conversation_id=conv_id,
    content="Hello world",
    message_type=MessageType.TEXT,
    attachments=[{"filename": "doc.pdf", "url": "/api/v1/media/attachments/123"}],
)
```

### Edit a message

```python
updated = message_service.edit_message(
    user_id=user_id,
    message_id=msg_id,
    content="Updated content",
    expected_edited_at=current_revision,  # optimistic concurrency
)
```

### Delete messages

```python
# Soft delete
message_service.delete_message(user_id, msg_id)

# Hard delete
message_service.delete_message(user_id, msg_id, hard_delete=True)

# Bulk delete
result = message_service.delete_messages_bulk(
    user_id, [msg_id_1, msg_id_2], conversation_id=conv_id
)
```

### Retrieve messages

```python
messages = message_service.get_messages(
    user_id=user_id,
    conversation_id=conv_id,
    limit=50,
    before_id=last_msg_id,  # cursor pagination
)
```

### Search messages

```python
results = message_service.search_messages(
    user_id=user_id,
    conversation_id=conv_id,
    query="important",
    limit=25,
)
```

## Configuration

| Config Key | Default | Description |
|------------|---------|-------------|
| `messaging.max_message_length` | 4000 | Max characters per message |
| `messaging.max_attachments_per_message` | 10 | Max attachments per message |
| `messaging.edit_window_seconds` | 0 (unlimited) | Time window for editing messages |
| `messaging.encrypt_messages` | false | Enable message encryption |
| `messaging.encrypt_attachments` | false | Encrypt attachment URLs |

## Related Code

- Message models: `src/core/messaging/models.py`
- Message repository: `src/core/messaging/repositories/message.py`
- Participant service: `src/core/messaging/services/participant.py`
- Content filter: `src/core/messaging/services/content_filter.py`
- Channel ratchet encryption: `src/utils/encryption/channel_ratchet.py`
- Search indexing: `src/core/search/__init__.py`
- Notification dispatch: `src/core/notifications/background.py`
