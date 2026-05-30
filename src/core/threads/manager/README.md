# Thread Manager

## Purpose
Implements thread lifecycle management, membership, and state transitions
for server-based thread conversations.

## Architecture
The ThreadManager is composed via mixin pattern. Each file in this directory
handles a specific domain:

| File | Mixin | Responsibilities |
|------|-------|-----------------|
| `helpers.py` | — | Standalone utility functions: `_row_to_thread`, `_row_to_thread_member`, `_get_channel`, `_get_message`, `_get_thread_internal`, `_check_auto_archive` |
| `permissions.py` | `ThreadPermissionMixin` | `can_view_thread`, `can_send_in_thread`, `can_manage_thread`, `_check_permission`, `_require_permission` |
| `creation.py` | `ThreadCreationMixin` | `create_thread`, `create_thread_from_message`, `_validate_thread_name` |
| `membership.py` | `ThreadMembershipMixin` | `join_thread`, `leave_thread`, `add_member`, `remove_member`, `get_thread_members`, `_is_member`, `_update_member_count`, `_get_member` |
| `messaging.py` | `ThreadMessagingMixin` | `send_message`, `get_messages`, `get_message_count` |
| `statemgmt.py` | `ThreadStateMixin` | `archive_thread`, `unarchive_thread`, `lock_thread`, `unlock_thread`, `update_thread`, `delete_thread`, `_unarchive_thread_internal` |
| `listing.py` | `ThreadListingMixin` | `get_thread`, `get_active_threads`, `get_archived_threads`, `get_user_threads`, `get_user_private_threads` |
| `manager.py` | `ThreadManager` | Composes all mixins with `BaseManager` |

## Primary Responsibilities
- Create, update, and archive threads
- Manage thread membership and permissions
- Maintain thread metadata and message counts
- Enforce thread naming and lock rules

## Core Components
- ThreadManager: main orchestration class for thread operations
- Thread models for state, type, and auto-archive duration

## Usage

```python
from src.core.threads.manager import ThreadManager

tm = ThreadManager(db, auth_module=auth, messaging_module=messaging,
                   servers_module=servers, notifications_module=notifications)

# Create a thread from a message
thread = tm.create_thread_from_message(
    user_id=1,
    message_id=42,
    name="Discussion Thread",
    auto_archive_duration=1440  # minutes
)

# Join a thread
tm.join_thread(user_id=2, thread_id=thread.id)

# Send a message in a thread
msg = tm.send_message(user_id=2, thread_id=thread.id, content="Great point!")

# Archive a thread
tm.archive_thread(user_id=1, thread_id=thread.id)

# Get user's active threads
threads = tm.get_user_threads(user_id=2)
```

## Error Handling

- `PermissionDeniedError` — User lacks `threads.view` or `threads.send` permission.
- `ValueError` — Invalid thread name, message ID not found, or thread not found.
- Archive/lock state transitions check current state and raise `ValueError` if the transition is invalid (e.g., archiving an already-archived thread).

```python
from src.core.servers.exceptions import PermissionDeniedError

try:
    thread = tm.create_thread_from_message(user_id=1, message_id=999, name="Test")
except ValueError as e:
    print(f"Thread creation failed: {e}")
except PermissionDeniedError as e:
    print(f"Permission denied: {e}")
```

## Dependencies
- Auth module for user validation.
- Messaging module for thread message interactions (message creation, retrieval).
- Servers module for permission checks (thread view/send/manage permissions).
- Notifications module for thread mention signals (when users are @mentioned in thread messages).

## Notes
- Auto-archive checks are based on last activity timestamps (via `_check_auto_archive` in helpers).
- Thread naming can be encrypted if `encryption.encrypt_thread_names` is enabled in config.
- Public API is exposed via `from .manager import ThreadManager`.
- Private threads enforce membership; public threads are visible to all channel members.
