# Messaging Repositories

Data access layer for the messaging system. Provides database CRUD operations with batch support, caching, and pagination. All repositories extend `BaseRepository`.

## Repository Files

### `base.py` - BaseRepository
Common base class for all repositories:
- `_execute()` / `_fetch_one()` / `_fetch_all()` - Standardized database operations
- `_build_in_clause()` - Safe IN clause construction for batch queries
- `_json_dumps()` / `_json_loads()` - JSON serialization helpers
- `begin_transaction()` / `commit()` / `rollback()` - Transaction delegation

### `message.py` - MessageRepository
Message data access:
- `create()` - Insert message with encrypted content support
- `get_by_id()` / `get_batch_by_ids()` - Single and batch retrieval
- `get_by_conversation()` - Paginated message list with cursor support (cached)
- `search()` - Text search with blind index optimization
- `update_content()` / `update_metadata()` - Edit operations
- `soft_delete()` / `hard_delete()` - Deletion methods
- `row_to_model()` - Decrypts content and converts to Message model

### `conversation.py` - ConversationRepository
Conversation data access:
- `create()` - Insert conversation with type, metadata, and encryption flag
- `get_by_id()` - Fetch with participant count subquery
- `get_user_conversations()` - Paginated user conversation list
- `update()` / `soft_delete()` - Mutation operations
- `get_dm_lookup()` / `create_dm_lookup()` / `delete_dm_lookup()` - DM deduplication
- `get_notes_conversation()` - Personal notes lookup

### `participant.py` - ParticipantRepository
Participant data access:
- `create()` / `create_bulk()` - Single and batch participant creation
- `get_by_conversation_and_user()` / `get_all_by_conversation()` - Retrieval
- `get_user_ids_by_conversation()` - For event routing
- `delete()` / `delete_bulk()` - Removal operations
- `update_role()` / `update_mute()` / `update_last_read()` - State updates
- `find_next_owner()` - Ownership transfer query
- `check_server_membership()` - Server-level access control

### `message_status.py` - MessageStatusRepository
Delivery and read status data access:
- `create()` - Insert initial SENT status
- `batch_mark_delivered()` / `batch_mark_read()` - Bulk status updates with upsert
- `get_batch_for_user()` / `get_batch_counts()` - Aggregated status queries
- `get_unread_count()` / `get_all_unread_counts()` - Unread counting
- `get_reader_ids()` / `get_batch_reader_ids()` - Read receipt queries

### `attachment.py` - AttachmentRepository
Attachment data access:
- `create()` / `create_bulk()` - Single and batch creation with encryption
- `get_by_id()` / `get_by_message()` / `get_batch_by_messages()` - Retrieval
- `count_by_message()` - Attachment count checks
- `soft_delete()` - Deletion
- `row_to_model()` - Decrypts URLs if encrypted

### `pin.py` - PinRepository
Pinned message data access:
- `create()` / `delete()` - Pin management
- `get_by_message()` / `get_batch_by_messages()` - Pin info retrieval
- `exists()` / `count_by_conversation()` - Pin limit checks
- `get_pinned_messages()` - Pins joined with message data

### `user_settings.py` - UserSettingsRepository
User settings data access:
- Message settings CRUD (get, create, update)
- Content filter settings CRUD (get, create, update)
- User existence checks (single and batch)

### `edit_history.py` - EditHistoryRepository
Edit history data access:
- `create()` - Store edit entry
- `get_by_message()` - Ordered history retrieval
- `get_latest_version()` - Version number tracking
- `delete_by_message()` - Cascade on message delete

## Query Patterns

- **? placeholders** used throughout, auto-converted to %s for PostgreSQL
- **IN clauses** built via `_build_in_clause()` for safe batch queries
- **Cursor pagination** used instead of OFFSET for message lists
- **Upsert** (ON CONFLICT) used for status updates to avoid duplicates
