# DM spam module

The DM spam module provides database schema definitions for the direct
message anti-spam system. It tracks spam filters and spam events to help
detect and prevent abusive direct message behavior.

## Tables

### `dm_spam_filters`

Stores spam filter rules configured by users. Each filter defines a
pattern, threshold, and action to take when the threshold is exceeded.

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER | Primary key |
| `user_id` | INTEGER | User who owns the filter |
| `target_user_id` | INTEGER | User the filter applies to (optional) |
| `pattern` | TEXT | Content pattern to match |
| `filter_type` | TEXT | Filter type (default `rate`) |
| `action` | TEXT | Action when triggered (default `warn`) |
| `threshold` | INTEGER | Trigger threshold (default 5) |
| `window_seconds` | INTEGER | Time window in seconds (default 60) |
| `enabled` | INTEGER | Whether the filter is active (default 1) |
| `created_at` | INTEGER | Unix timestamp of creation |
| `updated_at` | INTEGER | Unix timestamp of last update |

### `dm_spam_events`

Records individual spam events for analysis and enforcement.

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER | Primary key |
| `sender_id` | INTEGER | User who sent the message |
| `recipient_id` | INTEGER | User who received the message |
| `event_type` | TEXT | Type of spam event |
| `content_hash` | TEXT | Hash of the message content |
| `created_at` | INTEGER | Unix timestamp of creation |

## Usage

```python
from src.core.dmspam.schema import create_dmspam_tables

# Create all DM spam tables in the database.
create_dmspam_tables(db)
```

The `create_dmspam_tables` function executes the schema DDL against the
provided database connection. It converts schema types (for example,
`INTEGER` to `BIGINT`) when running on PostgreSQL.
