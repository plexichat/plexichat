# Search Services

Business logic layer for search-related operations within the search module.

## Files

### `saved_searches.py` - SavedSearchesService
Manages per-user saved search queries:

- `create_search()` - Save a named search query (max 50 per user)
- `get_search()` / `get_all_searches()` - Retrieval by ID or all for user
- `update_search()` - Update name and/or query
- `delete_search()` - Remove a saved search
- Validation: name max 100 chars, query max 500 chars, non-empty
