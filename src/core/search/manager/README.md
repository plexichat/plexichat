# SearchManager — Mixin Architecture

`SearchManager` is composed from 9 mixins + a base class to improve modularity and testability:

| File | Mixin | Methods |
|------|-------|---------|
| `base.py` | `SearchManagerBase` | `__init__`, `_load_config`, `_create_indexer`, `clear_cache`, `_get_manager` |
| `message_search.py` | `MessageSearchMixin` | `search_messages`, `search_messages_page`, `search_server_messages` |
| `message_indexing.py` | `MessageIndexingMixin` | `index_message`, `remove_from_index`, `reindex_all`, `reindex_conversation`, `_decrypt_message_content` |
| `user_search.py` | `UserSearchMixin` | `search_users`, `search_users_page`, `index_user` |
| `server_search.py` | `ServerSearchMixin` | `search_servers`, `search_servers_page`, `index_server` |
| `discovery.py` | `DiscoveryMixin` | `list_public_servers`, `get_server_categories`, `list_server`, `unlist_server`, `verify_server`, `bump_server`, `_enrich_server_results` |
| `query_utils.py` | `QueryUtilsMixin` | `parse_query`, `get_search_suggestions` |
| `rate_limiting.py` | `RateLimitMixin` | `_check_rate_limit` |
| `access.py` | `AccessControlMixin` | `_can_access_server`, `_get_accessible_conversations`, `_can_access_conversation`, `_can_access_channel`, `_get_server_member_ids`, `_get_user_server_ids`, `_get_user_servers_map` |
| `enrichment.py` | `EnrichmentMixin` | `_enrich_message_results`, `_enrich_user_results`, `_get_names` |
| `composer.py` | `SearchManager` | Combines all mixins with proper MRO |

## MRO

```
SearchManager
  → MessageSearchMixin
  → MessageIndexingMixin
  → UserSearchMixin
  → ServerSearchMixin
  → DiscoveryMixin
  → QueryUtilsMixin
  → RateLimitMixin
  → AccessControlMixin
  → EnrichmentMixin
  → SearchManagerBase
  → BaseManager
```

## Cross-Mixin Dependencies

Methods in one mixin may call methods in another. These are declared as stubs (ellipsis bodies) on `SearchManagerBase` so that pyright resolves them correctly:

- `MessageSearchMixin` → `_check_rate_limit` (RateLimitMixin), `_get_accessible_conversations` (AccessControlMixin), `_can_access_server` (AccessControlMixin), `_enrich_message_results` (EnrichmentMixin)
- `UserSearchMixin` → `_check_rate_limit` (RateLimitMixin), `_get_server_member_ids` (AccessControlMixin), `_enrich_user_results` (EnrichmentMixin)
- `ServerSearchMixin` → `_check_rate_limit` (RateLimitMixin), `_enrich_server_results` (DiscoveryMixin)
- `EnrichmentMixin` → `_get_user_server_ids` (AccessControlMixin), `_get_user_servers_map` (AccessControlMixin)

## Migration Note

Refactored from `manager.py` (1119 lines) into `manager/` package. The import path `src.core.search.manager → SearchManager` is preserved via `manager/__init__.py`.

All tests that do `from src.core.search.manager import SearchManager` continue to work unchanged.
