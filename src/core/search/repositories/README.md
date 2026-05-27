# Search Repositories

Data access layer for search-related database operations.

## Files

### `saved_searches.py` - SavedSearchesRepository
Database operations for saved searches:

- `create()` - Insert a new saved search record
- `get()` / `get_all()` - Retrieval by ID or all for a user
- `update()` - Update name and/or query fields
- `delete()` - Remove a saved search record
- `exists()` / `count()` - Validation and limit checks

Uses the `saved_searches` table with columns: id, user_id, name, query, created_at.
