# Messaging Services

Business logic layer for the messaging system. Each service encapsulates domain-specific operations with proper validation, authorization, and caching.

## Services

### `message.py` - MessageService
Core message operations:
- `send_message()` - Create and send messages with attachment, reply, and embed support
- `edit_message()` - Edit own messages with content validation
- `delete_message()` / `delete_messages_bulk()` - Soft/hard delete with permission checks
- `get_message()` / `get_messages()` - Retrieve messages with cursor pagination
- `search_messages()` - Full-text search within conversations
- `send_system_message()` - System-generated messages (join/leave notifications)
- `archive_messages_bulk()` - Bulk archive for cleanup
- Encryption handling via `src.utils.encryption`

### `conversation.py` - ConversationService
Conversation lifecycle:
- `create_dm()` - DM creation with auto-create and recipient settings checks
- `create_group()` - Group creation with name validation and content filtering
- `get_or_create_notes()` - Personal notes conversation for each user
- `create_server_channel_conversation()` - Server channel backing conversation
- `create_thread_conversation()` - Thread backing conversation
- `get_conversation()` / `get_conversations()` - Access-controlled retrieval
- `update_conversation()` - Name and participant limits updates
- `delete_conversation()` / `leave_conversation()` - Soft delete and leave

### `participant.py` - ParticipantService
Participant management:
- `add_participant()` / `remove_participant()` - Single participant operations
- `add_user_to_multiple_conversations()` / `remove_user_from_multiple_conversations()` - Batch operations
- `get_participant()` / `get_all_participants()` - Retrieval with caching
- `get_participant_ids()` - For event routing
- `is_participant()` - Membership check with server fallback
- `update_role()` / `update_mute()` / `update_last_read()` - State updates
- `find_next_owner()` - Ownership transfer on leave

### `message_status.py` - MessageStatusService
Delivery and read tracking:
- `mark_delivered()` / `mark_read()` - Batch status updates
- `get_unread_count()` - Per-conversation and total unread counts
- `get_message_status()` - Status details (sender only)
- `get_reader_ids()` / `get_batch_reader_ids()` - Read receipt queries
- `get_batch_status_info()` - Aggregated status with channel receipt settings

### `attachment.py` - AttachmentService
Attachment operations:
- `add_attachment()` - Add files with size and count validation
- `get_attachments()` - Retrieve message attachments
- `delete_attachment()` - Soft delete with ownership check
- `get_batch_by_messages()` - Batch fetch for message lists

### `content_filter.py` - ContentFilterService
Content filtering and validation:
- `get_filter_settings()` / `update_filter_settings()` - User filter preferences
- `validate_content()` - Single-pass validation with profanity, NSFW, and spoiler detection
- `_apply_user_filters()` - Optimized regex-based content filtering (CENSOR, SPOILER, BLOCK)

### `user_settings.py` - UserSettingsService
User messaging preferences:
- `get_message_settings()` / `update_message_settings()` - Message preferences
- `user_exists()` / `users_exist_batch()` - User existence checks (cached)

### `edit_history.py` - EditHistoryService
Message edit tracking:
- `record_edit()` - Store previous version on edit
- `get_edit_history()` - Retrieve full edit history
- `get_edit_count()` - Count edits for a message
- Optional encryption of stored edit content

### `pin.py` - PinService
Pinned messages:
- `pin_message()` / `unpin_message()` - Pin management with limits
- `get_pinned_messages()` - List all pins in conversation
- `get_batch_pin_info()` - Batch pin status

### `bookmarks.py` - BookmarkService
Per-user message bookmarks (independent of pins):
- `add_bookmark()` / `remove_bookmark()` - User-specific bookmarks
- `get_bookmarks()` - Paginated bookmark listing with content previews
- Max 200 bookmarks per user

### `forwarding.py` - ForwardingService
Message forwarding between conversations:
- `forward_message()` - Forward with attribution and permission checks
- Rate limited (50/hour) and capped (10 per message)
- Includes original author attribution

### `scheduled.py` - ScheduledMessageService
Future message delivery:
- `create_scheduled_message()` / `cancel_scheduled_message()` - Schedule management
- `dispatch_due_messages()` - Cron-triggered delivery
- Min 1 minute ahead, max 7 days
- Max 50 scheduled messages per user

### `voice.py` - VoiceMessageService
Voice message attachments:
- `validate_voice_attachment()` - Format, duration, and size validation
- Supported formats: ogg, mp3, wav, webm, opus
- Max 10 minutes duration, 25MB file size

### `last_chat.py` - LastChatService
Session continuity:
- `save_last_chat()` / `get_last_chat()` - Current conversation state
- `get_recent_chats()` - Recent conversation history (max 10)
- Used for reconnection state restoration

### `base.py` - BaseService
Common base class for all services:
- Snowflake ID generation (timestamp + machine_id + sequence)
- Two-tier caching (local memory + Redis)
- Config section loading
- Cache invalidation helpers

## Design Patterns

- **Service-Repository** pattern: Services handle business logic, Repositories handle data access
- **Two-tier caching**: Local memory (CappedDict) + Redis with TTL
- **Transaction-safe writes**: Multiple repository operations within a single transaction
- **Permission checks**: Every mutation verifies user authorization
