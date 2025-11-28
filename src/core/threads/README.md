# Threads Module

Thread management system for PlexiChat API supporting reply threads, forum-style threads, and thread membership.

## Features

- Thread creation:
  - Create thread from message (reply thread)
  - Create thread without message (forum-style)
  - Thread name (required, max 100 chars)
  - Auto-archive duration (1 hour, 24 hours, 3 days, 7 days)
  - Thread type (public, private, announcement)
- Thread membership:
  - Auto-join on thread creation
  - Join thread
  - Leave thread
  - Add member to thread
  - Remove member from thread
  - Get thread members
- Thread messages:
  - Send message to thread
  - Thread message count
  - Thread starter message reference
- Thread state:
  - Active / Archived / Locked
  - Archive thread (manual or auto after inactivity)
  - Unarchive thread (on new message or manual)
  - Lock thread (prevent new messages, moderator only)
  - Unlock thread
- Thread listing:
  - Get active threads in channel
  - Get archived threads in channel (paginated)
  - Get user's joined threads
  - Get user's private threads
- Thread permissions:
  - Inherit from parent channel
  - Private threads: only members can view
  - Create public threads permission
  - Create private threads permission
  - Manage threads permission (archive, lock, delete)
- Thread metadata:
  - Owner (creator)
  - Parent channel
  - Parent message (if created from message)
  - Created at, archived at, last message at
  - Message count, member count

## Setup

```python
from src.core.database import Database
from src.core import auth
from src.core import messaging
from src.core import servers
from src.core import notifications
from src.core import threads

# Initialize database
db = Database()
db.connect()

# Initialize dependencies
auth.setup(db)
messaging.setup(db, auth)
servers.setup(db, auth, messaging)
notifications.setup(db, messaging, servers)

# Initialize threads
threads.setup(db, auth, messaging, servers, notifications)
```

## Usage

### Create Thread

```python
from src.core import threads

# Create forum-style thread (no parent message)
thread = threads.create_thread(
    user_id=1,
    channel_id=123,
    name="Discussion Topic",
    thread_type=threads.ThreadType.PUBLIC,
    auto_archive_duration=threads.AutoArchiveDuration.ONE_DAY
)

# Create reply thread from message
thread = threads.create_thread_from_message(
    user_id=1,
    message_id=456,
    name="Follow-up Discussion"
)

# Create private thread
thread = threads.create_thread(
    user_id=1,
    channel_id=123,
    name="Private Discussion",
    thread_type=threads.ThreadType.PRIVATE
)
```

### Thread Membership

```python
# Join a thread
member = threads.join_thread(user_id=2, thread_id=thread.id)

# Leave a thread
threads.leave_thread(user_id=2, thread_id=thread.id)

# Add member to thread
member = threads.add_member(user_id=1, thread_id=thread.id, member_user_id=3)

# Remove member from thread
threads.remove_member(user_id=1, thread_id=thread.id, member_user_id=3)

# Get thread members
members = threads.get_thread_members(user_id=1, thread_id=thread.id)
```

### Thread Messages

```python
# Send message to thread
msg = threads.send_message(
    user_id=1,
    thread_id=thread.id,
    content="Hello thread!"
)

# Get messages
messages = threads.get_messages(user_id=1, thread_id=thread.id, limit=50)

# Get message count
count = threads.get_message_count(thread.id)
```

### Thread State

```python
# Archive thread
thread = threads.archive_thread(user_id=1, thread_id=thread.id)

# Unarchive thread
thread = threads.unarchive_thread(user_id=1, thread_id=thread.id)

# Lock thread (prevent new messages)
thread = threads.lock_thread(user_id=1, thread_id=thread.id)

# Unlock thread
thread = threads.unlock_thread(user_id=1, thread_id=thread.id)
```

### Thread Listing

```python
# Get active threads in channel
active = threads.get_active_threads(user_id=1, channel_id=123)

# Get archived threads (paginated)
archived = threads.get_archived_threads(
    user_id=1,
    channel_id=123,
    limit=50,
    before_timestamp=1700000000000
)

# Get user's joined threads
my_threads = threads.get_user_threads(user_id=1)

# Get user's private threads
private = threads.get_user_private_threads(user_id=1)
```

### Thread Info

```python
# Get thread
thread = threads.get_thread(user_id=1, thread_id=thread.id)

# Update thread
thread = threads.update_thread(
    user_id=1,
    thread_id=thread.id,
    name="New Name",
    auto_archive_duration=threads.AutoArchiveDuration.THREE_DAYS
)

# Delete thread
threads.delete_thread(user_id=1, thread_id=thread.id)
```

### Permission Checks

```python
# Check if user can view thread
can_view = threads.can_view_thread(user_id=2, thread_id=thread.id)

# Check if user can send messages
can_send = threads.can_send_in_thread(user_id=2, thread_id=thread.id)

# Check if user can manage thread
can_manage = threads.can_manage_thread(user_id=2, thread_id=thread.id)
```

## Thread Types

| Type | Description |
|------|-------------|
| public | Visible to all channel members |
| private | Only visible to thread members |
| announcement | Read-only for non-moderators |

## Thread States

| State | Description |
|-------|-------------|
| active | Thread is active and accepting messages |
| archived | Thread is archived (auto or manual) |
| locked | Thread is locked (no new messages) |

## Auto-Archive Durations

| Duration | Minutes |
|----------|---------|
| ONE_HOUR | 60 |
| ONE_DAY | 1440 |
| THREE_DAYS | 4320 |
| SEVEN_DAYS | 10080 |

## Permission Integration

| Permission | Description |
|------------|-------------|
| threads.create_public | Create public threads |
| threads.create_private | Create private threads |
| threads.manage | Archive, lock, delete threads |
| channels.view | View threads in channel |
| messages.send | Send messages in threads |

## Error Handling

All thread errors inherit from `ThreadError`:

```python
from src.core.threads import (
    ThreadError,
    ThreadNotFoundError,
    ThreadAccessDeniedError,
    ThreadArchivedError,
    ThreadLockedError,
    ThreadMemberNotFoundError,
    ThreadMemberExistsError,
    ThreadNameError,
    MessageNotFoundError,
    ChannelNotFoundError,
    PermissionDeniedError,
    InvalidThreadTypeError,
)

try:
    threads.send_message(user_id, thread_id, content)
except ThreadLockedError:
    print("Thread is locked")
except ThreadAccessDeniedError:
    print("Cannot access this thread")
except PermissionDeniedError as e:
    print(f"Missing permission: {e.permission}")
```

## Database Schema

Tables (prefixed with `thread_`):
- `thread_threads` - Thread metadata
- `thread_members` - Thread membership
- `thread_messages` - Thread message references

## Testing

```bash
pytest src/tests/threads/ -v
```
