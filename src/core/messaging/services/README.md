# Messaging services

This document describes the business logic layer for the messaging system. Each service encapsulates validation, authorization, encryption, and caching for a specific domain.

## Overview

Services sit between API handlers and repositories. They enforce permissions, validate input, handle encryption, and manage caching. Repositories handle data access; services handle business rules. Each service extends `BaseService` at `plexichat/src/core/messaging/services/base.py:51`.

## Base service

`base.py` provides shared behavior for all services:

*   **Snowflake ID generation.** Generates unique IDs from timestamp, machine ID, and sequence. See `base.py:86`.
*   **Two-tier caching.** Checks local `CappedDict` memory first, then Valkey (Redis). Writes to both with a 60-second TTL. Uses the `valkey` or legacy `redis` config block for `cache_max_items`.
*   **Configuration.** Loads the `messaging` config section. Use `get_encrypt_messages_default()` as the single source of truth for the `messaging.encrypt_messages` flag.
*   **Cache helpers.** `_cache_get`, `_cache_set`, and `_cache_invalidate` manage both cache tiers.

## Services

### MessageService

Core message operations. Defined in `message/__init__.py` and split across `message/send.py`, `message/edit_delete.py`, `message/retrieval.py`, and `message/composer.py`.

| Method | Description |
|--------|-------------|
| `send_message(...)` | Create and send a message with attachment, reply, and embed support. Handles encryption through `src.utils.encryption`. |
| `edit_message(...)` | Edit your own message with content validation. |
| `delete_message(...)` / `delete_messages_bulk(...)` | Soft-delete or hard-delete with permission checks. |
| `get_message(...)` / `get_messages(...)` | Retrieve messages with cursor pagination. |
| `search_messages(...)` | Full-text search within a conversation. |
| `send_system_message(...)` | Create system messages for join and leave events. |
| `archive_messages_bulk(...)` | Bulk-archive messages for cleanup. |

### ConversationService

Conversation lifecycle. Defined in `conversation.py`.

| Method | Description |
|--------|-------------|
| `create_dm(...)` | Create a DM with auto-create and recipient settings checks. Enforces block and friends-only rules through the relationships module. |
| `create_group(...)` | Create a group with name validation and content filtering. |
| `get_or_create_notes(...)` | Get or create the personal notes conversation for a user. |
| `create_server_channel_conversation(...)` | Create the backing conversation for a server channel. |
| `create_thread_conversation(...)` | Create the backing conversation for a thread. |
| `get_conversation(...)` / `get_conversations(...)` | Retrieve conversations with access control. |
| `update_conversation(...)` | Update name and participant limits. |
| `delete_conversation(...)` / `leave_conversation(...)` | Soft-delete a conversation or remove yourself from it. |

### ParticipantService

Participant management. Defined in `participant.py`.

| Method | Description |
|--------|-------------|
| `add_participant(...)` / `remove_participant(...)` | Add or remove a single participant. |
| `add_user_to_multiple_conversations(...)` / `remove_user_from_multiple_conversations(...)` | Batch add or remove. |
| `get_participant(...)` / `get_all_participants(...)` | Retrieve participants with caching. |
| `get_participant_ids(...)` | Get IDs for event routing. |
| `is_participant(...)` | Check membership with server fallback. |
| `update_role(...)` / `update_mute(...)` / `update_last_read(...)` | Update participant state. |
| `find_next_owner(...)` | Find the next owner when the current owner leaves. |

### MessageStatusService

Delivery and read tracking. Defined in `message_status.py`.

| Method | Description |
|--------|-------------|
| `mark_delivered(...)` / `mark_read(...)` | Batch-update status with upsert. |
| `get_unread_count(...)` | Get unread counts for one conversation or for all conversations. |
| `get_message_status(...)` | Get status details. Only the sender can view. |
| `get_reader_ids(...)` / `get_batch_reader_ids(...)` | Get read receipts. |
| `get_batch_status_info(...)` | Get aggregated status with channel receipt settings. |

### AttachmentService

Attachment operations. Defined in `attachment.py`.

| Method | Description |
|--------|-------------|
| `add_attachment(...)` | Add a file with size and count validation. |
| `get_attachments(...)` | Get attachments for a message. |
| `delete_attachment(...)` | Soft-delete with ownership check. |
| `get_batch_by_messages(...)` | Batch-fetch for message lists. |

### ContentFilterService

Content filtering and validation. Defined in `content_filter.py`.

| Method | Description |
|--------|-------------|
| `get_filter_settings(...)` / `update_filter_settings(...)` | Manage per-user filter preferences. |
| `validate_content(...)` | Validate content in a single pass with profanity, NSFW, and spoiler detection. |
| `_apply_user_filters(...)` | Apply regex-based filtering with `CENSOR`, `SPOILER`, and `BLOCK` actions. |

### UserSettingsService

User messaging preferences. Defined in `user_settings.py`.

| Method | Description |
|--------|-------------|
| `get_message_settings(...)` / `update_message_settings(...)` | Manage message preferences. |
| `user_exists(...)` / `users_exist_batch(...)` | Check user existence with caching. |

### EditHistoryService

Message edit tracking. Defined in `edit_history.py`.

| Method | Description |
|--------|-------------|
| `record_edit(...)` | Store the previous version when a message is edited. Optionally encrypts stored content. |
| `get_edit_history(...)` | Retrieve full history. |
| `get_edit_count(...)` | Count edits for a message. |

### PinService

Pinned messages. Defined in `pin.py`.

| Method | Description |
|--------|-------------|
| `pin_message(...)` / `unpin_message(...)` | Pin or unpin with limit checks. |
| `get_pinned_messages(...)` | List all pins in a conversation. |
| `get_batch_pin_info(...)` | Get pin status for multiple messages. |

### BookmarkService

Per-user bookmarks. Independent of pins. Defined in `bookmarks.py`.

| Method | Description |
|--------|-------------|
| `add_bookmark(...)` / `remove_bookmark(...)` | Add or remove a bookmark. Limited to 200 per user. |
| `get_bookmarks(...)` | List bookmarks with pagination and content previews. |

### ForwardingService

Message forwarding. Defined in `forwarding.py`.

| Method | Description |
|--------|-------------|
| `forward_message(...)` | Forward a message to another conversation with attribution and permission checks. Rate-limited to 50 per hour and capped at 10 forwards per source message. |

### ScheduledMessageService

Future delivery. Defined in `scheduled.py`.

| Method | Description |
|--------|-------------|
| `create_scheduled_message(...)` / `cancel_scheduled_message(...)` | Schedule or cancel a message. Requires at least 1 minute lead time and at most 7 days. Limited to 50 per user. |
| `dispatch_due_messages(...)` | Deliver due messages. Called by a cron job. |

### VoiceMessageService

Voice attachments. Defined in `voice.py`.

| Method | Description |
|--------|-------------|
| `validate_voice_attachment(...)` | Validate format, duration, and size. Supports `ogg`, `mp3`, `wav`, `webm`, and `opus`. Maximum 10 minutes and 25 MB. |

### LastChatService

Session continuity. Defined in `last_chat.py`.

| Method | Description |
|--------|-------------|
| `save_last_chat(...)` / `get_last_chat(...)` | Save and restore the current conversation. |
| `get_recent_chats(...)` | Get recent conversations, up to 10. Used for reconnection. |

### ExportService

Message export. Defined in `export.py`. Exports conversations for data portability.

## Design patterns

*   **Service-repository pattern.** Services contain business logic; repositories contain data access. Services call repositories, not the database directly.
*   **Two-tier caching.** Local `CappedDict` plus Valkey with TTL. See `base.py:109`.
*   **Transaction-safe writes.** Group multiple repository calls in a single transaction with `begin_transaction`, `commit`, and `rollback`.
*   **Permission checks.** Every mutation verifies that the user is authorized before writing.

## Related code

*   `plexichat/src/core/messaging/services/base.py` — base class and ID generation
*   `plexichat/src/core/messaging/repositories/` — data access layer
*   `plexichat/src/core/messaging/schema.py` — table definitions
*   `plexichat/src/core/messaging/models.py` — domain models
