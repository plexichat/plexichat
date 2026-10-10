# Messaging Tests

This directory contains tests for the messaging module, which implements
direct messaging (DM), group conversations, and message management.

## Test files

| File | Description |
|------|-------------|
| `test_messages.py` | Message CRUD operations: send, edit, and delete messages |
| `test_dm.py` | Direct message creation, retrieval, and sending |
| `test_conversations.py` | Conversation lifecycle: create, retrieve, update, delete, and leave |
| `test_groups.py` | Group conversation management, including owner roles and system messages |
| `test_attachments.py` | Message attachment upload, retrieval, and deletion |
| `test_pagination.py` | Cursor-based pagination for messages and conversations |
| `test_message_status.py` | Delivery and read status tracking, including unread counts |
| `test_participants.py` | Participant management: add, remove, mute, and permission checks |
| `test_content_filtering.py` | Content filter settings: profanity, NSFW, spoiler, and custom word lists |
| `test_edge_cases.py` | Error handling: nonexistent conversations, permissions, unicode, and search |

## Shared fixtures

The `conftest.py` file defines reusable fixtures:

- `dm_conversation` -- Creates a DM between two users from the shared pool.
- `fresh_dm` -- Creates isolated DM users for tests that require no shared state.
- `group_conversation` -- Creates a group conversation with an owner and two members.
- `users` -- Returns three users from the shared pool.

## Key behaviors tested

- **Idempotent DM creation:** Creating a DM with the same user pair returns
  the existing conversation.
- **System messages:** Group creation emits a `group_created` system message.
- **Search with encryption:** Message content is stored encrypted; substring
  search uses per-word blind indexes and handles multi-token queries.
- **Content filtering:** Users can configure profanity, NSFW, and custom word
  filters; spoiler click-to-reveal settings are also supported.
- **Participant constraints:** DMs reject additional participants; only
  conversation members can view or send messages.
