# Chat tracking module

The chat tracking module provides database schema definitions for tracking
user chat activity, webhook retry queues, and push notification tokens.

## Tables

### `webhook_retry_queue`

Stores per-attempt delivery metadata for webhook retries. The table is kept
in lockstep with `src/core/webhooks/retry.py`. Retries can redeliver
without losing delivery context such as event type, endpoint URL, headers,
response status, and delivery time.

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER | Primary key |
| `webhook_id` | INTEGER | Reference to the webhook |
| `event_type` | TEXT | Event that triggered the webhook |
| `payload` | TEXT | Request payload |
| `endpoint_url` | TEXT | Target URL |
| `attempt_count` | INTEGER | Number of delivery attempts |
| `max_attempts` | INTEGER | Maximum retry attempts (default 5) |
| `next_retry_at` | INTEGER | Unix timestamp for next retry |
| `last_error` | TEXT | Error message from the last attempt |
| `status` | TEXT | Delivery status (default `pending`) |
| `metadata` | TEXT | Additional delivery metadata |
| `response_status` | INTEGER | HTTP response status code |
| `delivered_at` | INTEGER | Unix timestamp of delivery |
| `created_at` | INTEGER | Unix timestamp of creation |
| `updated_at` | INTEGER | Unix timestamp of last update |

### `push_tokens`

Stores push notification tokens for users. The `token`, `p256dh`, and
`auth` columns hold encrypted envelopes. The `token_index` column is a
keyed blind index used for lookups on the plaintext.

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER | Primary key |
| `user_id` | INTEGER | Owner of the token |
| `token` | TEXT | Encrypted push token |
| `token_index` | TEXT | Blind index for lookups |
| `platform` | TEXT | Device platform |
| `device_id` | TEXT | Device identifier |
| `app_version` | TEXT | Application version |
| `p256dh` | TEXT | Encrypted push key |
| `auth` | TEXT | Push authentication secret |
| `created_at` | INTEGER | Unix timestamp of creation |
| `updated_at` | INTEGER | Unix timestamp of last update |
| `last_used_at` | INTEGER | Unix timestamp of last use |

### `user_last_chat`

Tracks the last conversation each user viewed.

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER | Primary key |
| `user_id` | INTEGER | Unique user identifier |
| `conversation_id` | INTEGER | Last viewed conversation |
| `last_message_id` | INTEGER | Last message in the conversation |
| `scroll_position` | INTEGER | Scroll position within the conversation |
| `updated_at` | INTEGER | Unix timestamp of last update |

### `user_recent_chats`

Stores a user's recently accessed conversations with unread counts.

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER | Primary key |
| `user_id` | INTEGER | Owner of the record |
| `conversation_id` | INTEGER | Conversation identifier |
| `accessed_at` | INTEGER | Unix timestamp of last access |
| `unread_count` | INTEGER | Number of unread messages (default 0) |

## Usage

```python
from src.core.chat_tracking.schema import create_chat_tracking_tables

# Create all chat tracking tables in the database.
create_chat_tracking_tables(db)
```

The `create_chat_tracking_tables` function executes the schema DDL against
the provided database connection. It converts schema types (for example,
`INTEGER` to `BIGINT`) when running on PostgreSQL.
