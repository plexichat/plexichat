# Search manager

This directory implements `SearchManager`, the central coordinator for search in PlexiChat. It delegates indexing to a pluggable backend and composes query parsing, filtering, ranking, access control, and discovery.

## Overview

`SearchManager` is not a single large class. It is assembled from nine mixins plus a base class so that each concern is isolated and testable. The public import path is stable:

```python
from plexichat.src.core.search.manager import SearchManager
```

This path is preserved by `manager/__init__.py` even though the implementation was refactored from a single `manager.py` (1119 lines) into the `manager/` package.

## Architecture

### Mixins

| File | Mixin | Responsibilities |
|------|-------|------------------|
| `base.py:34` | `SearchManagerBase` | Initialization, config loading, indexer creation and fallback, cache management |
| `message_search.py` | `MessageSearchMixin` | `search_messages`, `search_messages_page`, `search_server_messages` |
| `message_indexing.py` | `MessageIndexingMixin` | `index_message`, `remove_from_index`, `reindex_all`, `reindex_conversation`, `_decrypt_message_content` |
| `user_search.py` | `UserSearchMixin` | `search_users`, `search_users_page`, `index_user` |
| `server_search.py` | `ServerSearchMixin` | `search_servers`, `search_servers_page`, `index_server` |
| `discovery.py` | `DiscoveryMixin` | `list_public_servers`, `get_server_categories`, `list_server`, `unlist_server`, `verify_server`, `bump_server`, `_enrich_server_results` |
| `query_utils.py` | `QueryUtilsMixin` | `parse_query`, `get_search_suggestions` |
| `rate_limiting.py` | `RateLimitMixin` | `_check_rate_limit` |
| `access.py` | `AccessControlMixin` | `_can_access_server`, `_get_accessible_conversations`, `_can_access_conversation`, `_can_access_channel`, `_get_server_member_ids`, `_get_user_server_ids`, `_get_user_servers_map` |
| `enrichment.py` | `EnrichmentMixin` | `_enrich_message_results`, `_enrich_user_results`, `_get_names` |
| `composer.py:13` | `SearchManager` | Combines all mixins with the correct method resolution order (MRO) |

### Method resolution order

The MRO is defined in `composer.py:13` and determines which mixin method is called when names overlap:

```
SearchManager
  -> MessageSearchMixin
  -> MessageIndexingMixin
  -> UserSearchMixin
  -> ServerSearchMixin
  -> DiscoveryMixin
  -> QueryUtilsMixin
  -> RateLimitMixin
  -> AccessControlMixin
  -> EnrichmentMixin
  -> SearchManagerBase
  -> BaseManager
```

Do not change the order in `composer.py` without verifying that cross-mixin calls still resolve.

### Cross-mixin dependencies

A method in one mixin often calls a method defined in another mixin. `SearchManagerBase` in `base.py:194` declares these cross-mixin methods as stubs (`...`) so that static type checkers such as pyright resolve them correctly:

* `MessageSearchMixin` calls `_check_rate_limit` (RateLimitMixin), `_get_accessible_conversations` and `_can_access_server` (AccessControlMixin), and `_enrich_message_results` (EnrichmentMixin).
* `UserSearchMixin` calls `_check_rate_limit` (RateLimitMixin), `_get_server_member_ids` (AccessControlMixin), and `_enrich_user_results` (EnrichmentMixin).
* `ServerSearchMixin` calls `_check_rate_limit` (RateLimitMixin) and `_enrich_server_results` (DiscoveryMixin).
* `EnrichmentMixin` calls `_get_user_server_ids` and `_get_user_servers_map` (AccessControlMixin).

## Initialization and configuration

`SearchManagerBase.__init__` in `base.py:60` performs these steps:

1. Loads merged configuration in `_load_config()` (`base.py:100`) with defaults for `backend`, `batch_size`, `write_time_indexing`, `result_limit`, `elasticsearch`, `meilisearch`, `discovery`, and `rate_limit_per_minute`.
2. Creates an indexer in `_create_indexer()` (`base.py:126`). If the database type is `postgres` and the configured backend is `sqlite_fts5`, it selects `PostgresIndexer` automatically.
3. Calls `indexer.initialize()`. On failure, it attempts a low-resource fallback via `_create_fallback_indexer()` (`base.py:177`) — `PostgresIndexer` on Postgres, otherwise `SQLiteFTS5Indexer` — and initializes that instead.
4. Instantiates `QueryParser`, `FilterProcessor`, `RankingEngine`, and `DiscoveryManager`.
5. Initializes in-memory rate-limit counters.

To add a new backend, update `_create_indexer()` and `_create_fallback_indexer()` and add the corresponding config defaults.

## Key behaviors

* **Access control.** Message searches are scoped to accessible conversations. Use `_get_accessible_conversations()` and the `_can_access_*` helpers to enforce server, conversation, and channel visibility before returning results.
* **Enrichment.** Raw indexer results are enriched with display names and server metadata via `EnrichmentMixin` and `DiscoveryMixin`. Batch name lookups use `_get_names()` with a time-to-live cache.
* **Rate limiting.** Call `_check_rate_limit(user_id)` at the start of every user-facing search method. State is held in `_search_rate_window_started_ms` and `_search_rate_count` and can be cleared with `clear_cache()` (`base.py:190`).
* **Caching.** `clear_cache()` resets rate-limit windows. Name caches are managed separately inside `EnrichmentMixin` and `FilterProcessor`.

## Usage

```python
from plexichat.src.core.search.manager import SearchManager

manager = SearchManager(db, auth_module, messaging_module, servers_module)

# Search messages visible to user 123
results = manager.search_messages(user_id=123, query="from:me has:image hello", limit=25)

# Cursor pagination
page, next_cursor = manager.search_messages_page(
    user_id=123, query="hello", limit=25, cursor=None
)

# Index a new message
manager.index_message(message)
```
