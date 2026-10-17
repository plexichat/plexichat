# Messaging repositories

This document describes the data access layer for the messaging system. Use this layer when you need to read or write messaging data directly. All repositories extend `BaseRepository` and handle database operations, caching, and pagination.

## Overview

The repositories provide typed access to the `msg_*` tables. They handle SQL generation, JSON serialization, cursor pagination, and transaction delegation. Services call repositories; do not call repositories directly from API handlers.

## Base repository

`base.py` defines `BaseRepository`, the common base class for all repositories. It lives at `plexichat/src/core/messaging/repositories/base.py:12`.

| Method | Description |
|--------|-------------|
| `_execute(query, params, auto_commit)` | Execute a query without returning rows. |
| `_fetch_one(query, params)` | Fetch a single row as a dictionary. |
| `_fetch_all(query, params)` | Fetch all matching rows. |
| `_build_in_clause(items)` | Build a safe `IN` clause with `?` placeholders for batch queries. |
| `_json_dumps(data)` / `_json_loads(data)` | Serialize and deserialize JSON columns. |
| `begin_transaction()` / `commit()` / `rollback()` | Delegate transaction control to the underlying database. |

## Repositories

### MessageRepository

Defined in `message.py`. Provides access to `msg_messages` and `msg_message_tokens`.

| Method | Description |
|--------|-------------|
| `create(...)` | Insert a message. Supports encrypted content and blind-index columns. |
| `get_by_id(msg_id)` | Fetch a single message row by ID. |
| `get_batch_by_ids(msg_ids)` | Fetch multiple messages. Uses `_build_in_clause` for safe batching. |
| `get_by_conversation(conversation_id, limit, before_id, after_id)` | List messages with cursor pagination. Cached for 60 seconds. Includes deleted tombstones. |
| `search(conversation_id, search_query, ...)` | Search by plaintext `LIKE` and per-token blind indexes. Falls back to plaintext if `msg_message_tokens` does not exist. |
| `update_content(msg_id, content, ...)` | Update content. Preserves ciphertext on no-op edits and optionally suppresses the `edited` flag. |
| `update_metadata(msg_id, metadata, ...)` | Update the `metadata` JSON column. |
| `soft_delete(msg_id, ...)` | Blank content and set `deleted = 1` with `deleted_by` metadata. |
| `hard_delete(msg_id)` | Delete the row permanently. |
| `row_to_model(row, pin_info)` | Convert a row to a `Message` model. Decrypts `ENC:` payloads and legacy `content_encrypted` values. |

See `plexichat/src/core/messaging/repositories/message.py:41` for implementation details, including `tokenize_search_words` and `_escape_like`.

### ConversationRepository

Defined in `conversation.py`. Provides access to `msg_conversations` and `msg_dm_lookup`.

| Method | Description |
|--------|-------------|
| `create(...)` | Insert a conversation with type, metadata, and the `encrypted` flag. |
| `get_by_id(conversation_id)` | Fetch a conversation with a participant-count subquery. |
| `get_user_conversations(user_id, ...)` | List conversations for a user with pagination. |
| `update(conversation_id, ...)` / `soft_delete(conversation_id)` | Mutate or soft-delete a conversation. |
| `get_dm_lookup(user_a, user_b)` / `create_dm_lookup(...)` / `delete_dm_lookup(...)` | Deduplicate direct-message conversations. |
| `get_notes_conversation(user_id)` | Get the personal notes conversation for a user. |

### ParticipantRepository

Defined in `participant.py`. Provides access to `msg_participants`.

| Method | Description |
|--------|-------------|
| `create(...)` / `create_bulk(...)` | Add one or many participants. |
| `get_by_conversation_and_user(conversation_id, user_id)` / `get_all_by_conversation(conversation_id)` | Retrieve participants. |
| `get_user_ids_by_conversation(conversation_id)` | Get user IDs for event routing. |
| `delete(...)` / `delete_bulk(...)` | Remove participants. |
| `update_role(...)` / `update_mute(...)` / `update_last_read(...)` | Update participant state. |
| `find_next_owner(conversation_id)` | Find the next owner for ownership transfer. |
| `check_server_membership(user_id, server_id)` | Check server-level access. |

### MessageStatusRepository

Defined in `message_status.py`. Provides access to `msg_message_status`.

| Method | Description |
|--------|-------------|
| `create(...)` | Insert an initial `SENT` status. |
| `batch_mark_delivered(...)` / `batch_mark_read(...)` | Bulk update with `ON CONFLICT` upsert. |
| `get_batch_for_user(...)` / `get_batch_counts(...)` | Aggregated status queries. |
| `get_unread_count(...)` / `get_all_unread_counts(...)` | Count unread messages. |
| `get_reader_ids(...)` / `get_batch_reader_ids(...)` | Get read receipts. |

### AttachmentRepository

Defined in `attachment.py`. Provides access to `msg_attachments`.

| Method | Description |
|--------|-------------|
| `create(...)` / `create_bulk(...)` | Create attachments with optional encryption. |
| `get_by_id(attachment_id)` / `get_by_message(message_id)` / `get_batch_by_messages(message_ids)` | Retrieve attachments. |
| `count_by_message(message_id)` | Count attachments for limit checks. |
| `soft_delete(attachment_id)` | Soft-delete an attachment. |
| `row_to_model(row)` | Convert a row to a model. Decrypts URLs when encrypted. |

### PinRepository

Defined in `pin.py`. Provides access to `msg_pinned`.

| Method | Description |
|--------|-------------|
| `create(...)` / `delete(...)` | Pin or unpin a message. |
| `get_by_message(message_id)` / `get_batch_by_messages(message_ids)` | Get pin info. |
| `exists(message_id)` / `count_by_conversation(conversation_id)` | Check pin limits. |
| `get_pinned_messages(conversation_id)` | List pins joined with message data. |

### UserSettingsRepository

Defined in `user_settings.py`. Provides access to `msg_user_settings` and `msg_content_filters`.

| Method | Description |
|--------|-------------|
| `get_message_settings(user_id)` / `create_message_settings(...)` / `update_message_settings(...)` | Manage message preferences. |
| `get_content_filter_settings(user_id)` / `create_content_filter_settings(...)` / `update_content_filter_settings(...)` | Manage content filter preferences. |
| `user_exists(user_id)` / `users_exist_batch(user_ids)` | Check user existence. |

### EditHistoryRepository

Defined in `edit_history.py`. Provides access to `msg_edit_history`.

| Method | Description |
|--------|-------------|
| `create(message_id, ...)` | Store a previous version of a message. |
| `get_by_message(message_id)` | Get history ordered by version. |
| `get_latest_version(message_id)` | Get the current version number. |
| `delete_by_message(message_id)` | Delete history when a message is hard-deleted. |

## Query patterns

Use these patterns consistently when you add queries:

*   **Placeholders.** Write queries with `?` placeholders. The database layer converts them to `%s` for PostgreSQL.
*   **Batch queries.** Use `_build_in_clause(items)` to build `IN` clauses safely. Do not interpolate ID lists directly.
*   **Pagination.** Use cursor pagination (`before_id` / `after_id` on Snowflake IDs) instead of `OFFSET`. See `message.py:109` for an example.
*   **Upserts.** Use `ON CONFLICT` for status updates to avoid duplicate rows. See `message_status.py:44`.

## Cache invalidation

`MessageRepository` invalidates `messages_list` and `messages_api` caches after writes through `invalidate_conversation_cache`. See `plexichat/src/core/messaging/repositories/message.py:410`.

## Related code

*   `plexichat/src/core/messaging/repositories/base.py` — base class
*   `plexichat/src/core/messaging/repositories/__init__.py` — public exports
*   `plexichat/src/core/messaging/schema.py` — table definitions
