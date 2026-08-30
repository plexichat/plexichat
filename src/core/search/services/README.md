# Search services

This directory contains the business-logic layer for search. Services validate input, enforce limits, and delegate persistence to repositories.

## Overview

Services sit between the manager or API layer and the repositories. A service method validates arguments, checks quotas, calls the repository, and returns the resulting data. Repositories handle SQL; services handle rules.

All IDs are Snowflake IDs (`SnowflakeID` from `src.core.base`). Timestamps are generated with `timestamp_ms()`.

## Saved searches — `saved_searches.py:11`

`SavedSearchesService` manages per-user saved search queries. It wraps `SavedSearchesRepository` (`repositories/saved_searches.py:9`).

### Limits and validation

The service enforces these rules on every write:

| Rule | Value | Error |
|------|-------|-------|
| Name required | Non-empty after trimming | `ValueError: Name cannot be empty` |
| Query required | Non-empty after trimming | `ValueError: Query cannot be empty` |
| Name length | At most 100 characters | `ValueError: Name cannot exceed 100 characters` |
| Query length | At most 500 characters | `ValueError: Query cannot exceed 500 characters` |
| Per-user quota | At most 50 saved searches | `ValueError: Maximum saved searches limit reached (50)` |
| Update requires a field | At least one of `name` or `query` | `ValueError: Either name or query must be provided` |
| Target must exist | Row with matching `id` and `user_id` | `ValueError: Saved search not found` |

The per-user quota is checked via `SavedSearchesRepository.count()` in `saved_searches.py:54` before insertion.

### Methods

| Method | Description |
|--------|-------------|
| `create_search(user_id, name, query)` (`saved_searches.py:21`) | Validate, generate an ID and timestamp, insert the row, and return the created record. Raises `ValueError` or `RuntimeError` on failure. |
| `get_search(search_id, user_id)` (`saved_searches.py:75`) | Return one saved search or `None` if not found. |
| `get_all_searches(user_id)` (`saved_searches.py:92`) | Return all saved searches for a user, newest first. |
| `update_search(search_id, user_id, name, query)` (`saved_searches.py:104`) | Validate the new values, verify ownership, update the row, and return the updated record. |
| `delete_search(search_id, user_id)` (`saved_searches.py:154`) | Delete the row and return `True`. |

### Usage

```python
from plexichat.src.core.search.services.saved_searches import SavedSearchesService

service = SavedSearchesService(db)

# Create
saved = service.create_search(user_id=123, name="Images from me", query="from:me has:image")

# Read
item = service.get_search(saved["id"], user_id=123)
all_items = service.get_all_searches(user_id=123)

# Update
updated = service.update_search(saved["id"], user_id=123, name="Updated name")

# Delete
service.delete_search(saved["id"], user_id=123)
```

Handle validation errors at the call site:

```python
try:
    service.create_search(user_id, name, query)
except ValueError as e:
    # Return a 400 response with str(e)
    ...
```
