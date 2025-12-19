# Messaging Module

Secure messaging system for PlexiChat API supporting direct messages, group conversations, rich text formatting, attachments, and delivery/read receipts.

## Features

- Direct messages (DMs) with auto-create option
- Group conversations with roles (owner, admin, member)
- Snowflake IDs for distributed unique identification
- Rich text formatting (bold, italic, spoilers, code blocks, etc.)
- Content filtering (profanity, NSFW, custom blocked words)
- Message attachments with configurable limits
- Delivery and read receipts
- Message pinning
- Soft delete with audit trail
- Zero-friction at-rest encryption
- Per-user configurable limits

## Installation

Requires the following packages (already installed for auth module):

```bash
pip install argon2-cffi cryptography PyYAML
```

## Setup

```python
from src.core.database import Database
from src.core import auth
from src.core import messaging

# Initialize database
db = Database()
db.connect()

# Initialize auth first (messaging depends on users)
auth.setup(db)

# Initialize messaging
messaging.setup(db)
```

## Usage

### Direct Messages

```python
from src.core import messaging

# Create or get existing DM
dm = messaging.create_dm(user_id=1, recipient_id=2)

# Send a message
msg = messaging.send_message(
    user_id=1,
    conversation_id=dm.id,
    content="Hello! How are you?"
)

# Get messages
messages = messaging.get_messages(user_id=1, conversation_id=dm.id, limit=50)
```

### Group Conversations

```python
# Create a group
group = messaging.create_group(
    owner_id=1,
    name="Project Team",
    participant_ids=[2, 3, 4]
)

# Add participant
messaging.add_participant(
    user_id=1,  # Must be owner or admin
    conversation_id=group.id,
    participant_id=5,
    role=messaging.ParticipantRole.MEMBER
)

# Update participant role
messaging.update_participant_role(
    user_id=1,
    conversation_id=group.id,
    participant_id=5,
    role=messaging.ParticipantRole.ADMIN
)

# Remove participant
messaging.remove_participant(user_id=1, conversation_id=group.id, participant_id=5)
```

### Rich Text Formatting

Messages support markdown-style formatting:

```python
# Bold
msg = messaging.send_message(user_id, conv_id, "This is **bold** text")

# Italic
msg = messaging.send_message(user_id, conv_id, "This is *italic* text")

# Spoiler (hidden until clicked)
msg = messaging.send_message(user_id, conv_id, "The answer is ||42||")

# Code
msg = messaging.send_message(user_id, conv_id, "Use `print()` function")

# Code block
msg = messaging.send_message(user_id, conv_id, """```python
def hello():
    print("Hello!")
```""")

# Strikethrough
msg = messaging.send_message(user_id, conv_id, "~~wrong~~ correct")

# Quote
msg = messaging.send_message(user_id, conv_id, "> This is a quote")
```

### Message Operations

```python
# Edit message (own only)
msg = messaging.edit_message(user_id=1, message_id=msg.id, content="Updated content")

# Delete message (soft delete)
messaging.delete_message(user_id=1, message_id=msg.id)

# Pin message
messaging.pin_message(user_id=1, message_id=msg.id)

# Get pinned messages
pinned = messaging.get_pinned_messages(user_id=1, conversation_id=conv_id)

# Reply to message
reply = messaging.send_message(
    user_id=1,
    conversation_id=conv_id,
    content="I agree!",
    reply_to_id=msg.id
)
```

### Attachments

```python
# Send message with attachment
msg = messaging.send_message(
    user_id=1,
    conversation_id=conv_id,
    content="Check out this file",
    attachments=[{
        "filename": "document.pdf",
        "content_type": "application/pdf",
        "size": 1024000,
        "url": "https://storage.example.com/files/doc.pdf"
    }]
)

# Get attachments
attachments = messaging.get_attachments(user_id=1, message_id=msg.id)

# Delete attachment
messaging.delete_attachment(user_id=1, attachment_id=attachments[0].id)
```

### Read Receipts

```python
# Mark messages as delivered
messaging.mark_delivered(user_id=2, message_ids=[msg1.id, msg2.id])

# Mark conversation as read (up to specific message)
messaging.mark_read(user_id=2, conversation_id=conv_id, up_to_message_id=msg.id)

# Get unread counts
unread = messaging.get_unread_count(user_id=2)
# Returns: {conversation_id: count, ...}

# Get message status (sender only)
status = messaging.get_message_status(user_id=1, message_id=msg.id)
for s in status:
    print(f"User {s.user_id}: {s.status.value} at {s.timestamp}")
```

### Content Filtering

```python
# Get user's filter settings
filters = messaging.get_user_filter_settings(user_id=1)

# Update filter settings
messaging.update_user_filter_settings(
    user_id=1,
    profanity_filter=True,
    nsfw_filter=True,
    spoiler_click_to_reveal=True,
    custom_blocked_words=["spam", "advertisement"]
)
```

### User Message Settings

```python
# Get user's message settings
settings = messaging.get_user_message_settings(user_id=1)

# Update settings
messaging.update_user_message_settings(
    user_id=1,
    allow_dms_from="everyone",  # "everyone", "friends", "none"
    auto_create_dms=True,
    max_message_length=8000,  # Override global default
    max_attachment_size=20971520,  # 20MB
    max_attachments_per_message=5
)
```

### Pagination

Messages use cursor-based pagination with Snowflake IDs:

```python
# Get first page
messages = messaging.get_messages(user_id=1, conversation_id=conv_id, limit=50)

# Get older messages (before cursor)
if messages:
    older = messaging.get_messages(
        user_id=1,
        conversation_id=conv_id,
        limit=50,
        before_id=messages[-1].id
    )

# Get newer messages (after cursor)
if messages:
    newer = messaging.get_messages(
        user_id=1,
        conversation_id=conv_id,
        limit=50,
        after_id=messages[0].id
    )
```

## Configuration

All settings are in `config/config.yaml` under `messaging`:

```yaml
messaging:
  # Message limits
  max_message_length: 4000
  max_group_participants: 100
  message_preview_length: 100
  
  # Attachment limits
  max_attachment_size: 10485760  # 10MB
  max_attachments_per_message: 10
  
  # DM settings
  dm_auto_create: true
  
  # Encryption
  encrypt_messages: true
  encrypt_attachments: true
  
  # Content filtering
  content:
    profanity_words: []
    nsfw_patterns: []
    default_filter_action: censor  # censor, block, warn, spoiler
```

## Permission Integration

The messaging module integrates with the auth module's permission system:

| Permission | Description |
|------------|-------------|
| messages.send | Send messages in conversations |
| messages.read | Read messages in conversations |
| messages.edit | Edit own messages |
| messages.delete | Delete own messages |
| messages.delete_others | Delete others' messages (moderator) |
| messages.pin | Pin messages |
| messages.react | Add reactions to messages |
| conversations.create | Create new conversations |
| conversations.join | Join conversations |
| conversations.leave | Leave conversations |
| conversations.invite | Invite others to conversations |
| conversations.kick | Remove others from conversations |
| conversations.manage | Manage conversation settings |
| conversations.delete | Delete conversations |

## Participant Roles

| Role | Capabilities |
|------|-------------|
| owner | Full control, can delete conversation, manage all participants |
| admin | Can add/remove members, manage settings |
| member | Can send messages, leave conversation |

## Security Features

1. **Content Validation**: All content validated against SQL injection and XSS
2. **Encryption**: Zero-friction at-rest encryption for messages and attachments
3. **Snowflake IDs**: Prevents enumeration attacks
4. **Soft Deletes**: Maintains audit trail
5. **Permission Checks**: Every operation validates user permissions
6. **Rate Limiting**: Prepared hooks for rate limiting (future)

## Error Handling

All messaging errors inherit from `MessagingError`:

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
    print(f"Message too long: {e.actual_length}/{e.max_length}")
except InvalidContentError as e:
    print(f"Invalid content: {e.issues}")
except ConversationAccessDeniedError:
    print("You don't have access to this conversation")
```

## Testing

```bash
pytest src/tests/messaging/ -v
```

## Database Schema

Tables (prefixed with `msg_`):
- `msg_conversations` - Conversation metadata
- `msg_participants` - Conversation participants
- `msg_messages` - Message content
- `msg_message_status` - Delivery/read receipts
- `msg_pinned` - Pinned messages
- `msg_attachments` - Message attachments
- `msg_content_filters` - User content filter settings
- `msg_user_settings` - User message settings
- `msg_dm_lookup` - Quick DM lookup table
