# Search repositories

This directory contains the data-access layer for search. Repositories execute SQL directly and contain no business logic or validation.

## Overview

Repositories wrap the database handle (`db`) and expose typed methods for persistence. Business rules such as character limits and per-user quotas live in the services layer, not here.

All repositories use Snowflake IDs (`SnowflakeID` from `src.core.base`) for primary keys.

## Saved searches — `saved_searches.py:9`

`SavedSearchesRepository` persists per-user saved search queries in the `saved_searches` table.

### Table schema

| Column | Type | Description |
|--------|------|-------------|
| `id` | `SnowflakeID` | Primary key |
| `user_id` | `SnowflakeID` | Owner of the saved search |
| `name` | `TEXT` | Display name |
| `query` | `TEXT` | Raw query string |
| `created_at` | `INTEGER` | Creation timestamp in milliseconds |

### Methods

| Method | Signature | Description |
|--------|-----------|-------------|
| `create()` | `create(search_id, user_id, name, query, created_at)` (`saved_searches.py:16`) | Insert a row. The caller generates the ID and timestamp. |
| `get()` | `get(search_id, user_id)` (`saved_searches.py:32`) | Return one row or `None`. Scoped to the owning user. |
| `get_all()` | `get_all(user_id)` (`saved_searches.py:43`) | Return all rows for a user ordered by `created_at DESC`. |
| `update()` | `update(search_id, user_id, name, query)` (`saved_searches.py:53`) | Update `name` and/or `query` if provided. Returns `False` if neither is provided. |
| `delete()` | `delete(search_id, user_id)` (`saved_searches.py:82`) | Delete the row. Returns `True` unconditionally. |
| `exists()` | `exists(search_id, user_id)` (`saved_searches.py:90`) | Return `True` if the row exists. |
| `count()` | `count(user_id)` (`saved_searches.py:98`) | Return the number of saved searches for a user. Used for quota checks. |

### Usage

```python
from plexichat.src.core.search.repositories.saved_searches import SavedSearchesRepository
from plexichat.src.core.base import generate_id, timestamp_ms

repo = SavedSearchesRepository(db)

search_id = generate_id()
repo.create(search_id, user_id, name="My search", query="from:me has:image", created_at=timestamp_ms())

row = repo.get(search_id, user_id)
all_rows = repo.get_all(user_id)

repo.update(search_id, user_id, name="Updated name")
repo.delete(search_id, user_id)
```

All queries are scoped by both `id` and `user_id` so that one user cannot read or modify another user's saved searches. The repository does not enforce name or query length — that is the responsibility of `services/saved_searches.py`.
