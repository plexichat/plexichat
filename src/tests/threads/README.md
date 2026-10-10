# Threads Tests

This directory contains tests for the threads module, which implements
threaded conversations within channels.

## Test files

| File | Description |
|------|-------------|
| `test_create_thread.py` | Thread creation: public, private, announcement, and name validation |
| `test_messages.py` | Sending, retrieving, and counting messages in threads |
| `test_listing.py` | Listing active, archived, and user-specific threads |
| `test_archive.py` | Archive, unarchive, and auto-archive duration management |
| `test_lock.py` | Lock and unlock behavior, including owner override |
| `test_membership.py` | Join, leave, add, remove, and member count tracking |
| `test_permissions.py` | View, send, and manage permission checks |
| `test_private_threads.py` | Private thread visibility, membership, and message access |
| `test_edge_cases.py` | Deleted threads, invalid operations, name validation, and state transitions |
| `test_integration.py` | End-to-end thread workflows |

## Shared fixtures

The `conftest.py` file defines reusable fixtures:

- `server_with_channel` -- Creates a server with a text channel and member
  roles for thread tests.
- `server_with_moderator` -- Creates a server with a moderator role that has
  full thread management permissions.
- `thread_manager` -- Provides a bare `ThreadManager` with mocked permissions
  for low-level unit tests.
- `fresh_users` -- Creates isolated users for tests requiring no shared state.

## Key behaviors tested

- **Creator auto-join:** Thread creators are automatically added as members.
- **Message auto-join:** Sending a message to a public thread adds the sender
  as a member.
- **Lock override:** Thread owners can send messages to locked threads;
  non-owners cannot.
- **Archive-unarchive cycle:** Sending a message to an archived thread
  automatically unarchives it.
- **Name validation:** Empty, whitespace-only, and >100-character names are
  rejected; names are trimmed.
- **Private thread access:** Non-members cannot view, send, or join private
  threads; only owners can add members.
- **Moderator powers:** Moderators with `threads.manage` can archive, lock,
  delete, and update any thread.
