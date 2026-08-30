# Search and discovery

Add full-text search and server discovery to Plexichat.

## Overview

The search module provides full-text search over messages, users, and servers. It supports advanced query syntax, permission-aware filtering, pluggable indexer backends, and a public server directory with categories and verification.

Use this module to implement message search, user and server lookup, and the server discovery directory.

## Before you begin

Initialize the module once at startup with a connected database and required dependencies.

```python
from plexichat.src.core.database import Database
from plexichat.src.core import auth, messaging, servers, search

db = Database()
db.connect()

auth.setup(db)
messaging.setup(db, auth)
servers.setup(db, auth, messaging)

search.setup(db, auth, messaging, servers)
```

`__init__.py:115` creates a `SearchManager` singleton. All public functions delegate through `_get_manager` (`__init__.py:133`), which raises `RuntimeError` if you call it before setup.

## Search messages

### Basic search

```python
from plexichat.src.core import search

results = search.search_messages(user_id=1, query="hello world")
```

### Filtered search

```python
# Advanced query syntax
results = search.search_messages(user_id=1, query='from:alice "exact phrase" has:image')

# Scope to a server or channel
results = search.search_messages(user_id=1, query="meeting notes", server_id=123)
results = search.search_messages(user_id=1, query="bug report", channel_id=456)
```

### Query syntax

Parsed by `query/parser.py` and modeled in `models.py:19`:

| Filter | Description | Example |
|--------|-------------|---------|
| `from:user` | Messages from a user | `from:alice` |
| `in:channel` | Messages in a channel | `in:general` |
| `before:date` | Messages before a date | `before:2024-01-01` |
| `after:date` | Messages after a date | `after:7d` |
| `has:type` | Messages with attachments | `has:image`, `has:link`, `has:file` |
| `mentions:user` | Messages that mention a user | `mentions:bob` |
| `pinned:true` | Pinned messages | `pinned:true` |
| `"phrase"` | Exact phrase match | `"hello world"` |
| `-filter:value` | Negated filter | `-from:bot` |

Date formats:

* ISO: `2024-01-15`
* Relative: `7d` (days), `2w` (weeks), `1m` (months), `1y` (years)
* Keywords: `today`, `yesterday`

### Cursor pagination

Use the `*_page` variants for cursor-based pagination:

```python
page = search.search_messages_page(
    user_id=1,
    query="hello",
    limit=25,
    cursor=None,
)
# page.results, page.next_cursor  (models.py:147)
```

## Search users and servers

```python
# Search users by username or display name
results = search.search_users(user_id=1, query="alice")
results = search.search_users(user_id=1, query="mod", server_id=123)

# Paginated
page = search.search_users_page(user_id=1, query="alice", limit=25)

# Search public servers
results = search.search_servers(user_id=1, query="gaming")
results = search.search_servers(user_id=1, query="minecraft", category="gaming")

# Paginated
page = search.search_servers_page(user_id=1, query="gaming", category="gaming", limit=25)
```

User and server search results are defined in `models.py:104` and `models.py:118`. Server search respects categories and tags indexed from discovery listings.

## Use server discovery

Discovery manages public server listings, categories, bumping, and verification. Logic is in `discovery/manager.py:23`.

```python
# List public servers
servers = search.list_public_servers(category="gaming", sort_by="member_count", limit=25)
# sort_by: "member_count", "bumped_at", or "created_at" (discovery/manager.py:257)

# Get available categories
categories = search.get_server_categories()
# Returns list[ServerCategory] (models.py:171)

# List your server
listing = search.list_server(
    user_id=owner_id,
    server_id=123,
    category="gaming",
    description="A friendly gaming community",
    tags=["minecraft", "survival", "friendly"],
)

# Bump your server (requires membership; 4-hour cooldown by default)
search.bump_server(user_id=1, server_id=123)

# Remove from directory
search.unlist_server(user_id=owner_id, server_id=123)

# Verify (admin)
search.verify_server(server_id=123, level=search.VerificationLevel.VERIFIED)
```

Permissions and limits (`discovery/manager.py:52`):

* `list_server` and `unlist_server` require `server.manage`.
* `bump_server` requires server membership and respects `bump_cooldown_hours`.
* `list_server` requires `min_members_for_listing` members.
* If the servers module is not configured, listing and bumping fail closed with `SearchPermissionError` (`discovery/manager.py:347`).
* `bump_server` returns `False` for missing listings or permission failures to preserve the legacy boolean API (`discovery/manager.py:196`); explicit `unlist_server` raises `ServerNotListedError`.

See the [Server discovery README](discovery/README.md) for component details.

## Index messages

Messages are indexed automatically when `write_time_indexing` is `true`. For manual control:

```python
# Index a message
search.index_message(
    message_id=123,
    content="Hello world",
    metadata={
        "author_id": 1,
        "conversation_id": 456,
        "server_id": 789,
        "channel_id": 101,
        "created_at": 1699999999000,
        "has_attachments": False,
    },
)

# Remove from index
search.remove_from_index(message_id=123)

# Reindex a conversation
search.reindex_conversation(conversation_id=456)
```

Integrate with the messaging module to keep the index consistent:

```python
# After sending
search.index_message(msg.id, msg.content, metadata)

# After editing
search.index_message(msg.id, msg.content, metadata)

# After deleting
search.remove_from_index(msg.id)
```

## Parse queries and get suggestions

```python
# Parse a query
parsed = search.parse_query('from:alice hello world has:image')
print(parsed.search_terms)   # ['hello', 'world']
print(parsed.filters)        # [QueryFilter(FROM_USER, 'alice'), ...]
print(parsed.exact_phrases)  # []

# Get completions for a partial query
suggestions = search.get_search_suggestions(user_id=1, partial_query="from:")
```

`ParsedQuery` and `QueryFilter` are defined in `models.py:43` and `models.py:52`.

## Configuration

Add to `config/config.yaml`:

```yaml
search:
  # Backend: sqlite_fts5 (default), elasticsearch, meilisearch
  backend: sqlite_fts5
  batch_size: 100
  write_time_indexing: true
  result_limit: 100
  elasticsearch:
    hosts:
      - http://localhost:9200
    index_prefix: plexichat
  meilisearch:
    host: http://localhost:7700
    api_key: null
    index_prefix: plexichat
  discovery:
    min_members_for_listing: 10
    bump_cooldown_hours: 4
    max_tags: 10
    categories:
      - gaming
      - music
      - entertainment
      - education
      - science
      - creative
      - social
      - sports
      - finance
      - other
```

Defaults are loaded in `manager/composer.py` and `discovery/manager.py:33`.

## Choose a search backend

### SQLite FTS5 (default)

Uses SQLite's FTS5 extension with Porter stemming. Defined in `indexer/sqlite_fts5.py`.

* **Pros:** No external dependencies, zero configuration, good for small to medium deployments.
* **Cons:** Limited scalability, basic relevance ranking.

### Elasticsearch

Production-grade search for large deployments (`indexer/elasticsearch.py`).

```yaml
search:
  backend: elasticsearch
  elasticsearch:
    hosts:
      - http://localhost:9200
    index_prefix: plexichat
```

* **Pros:** Highly scalable, advanced relevance tuning, rich query DSL.
* **Cons:** Requires an external service, more complex setup.

### Meilisearch

Fast, typo-tolerant engine (`indexer/meilisearch.py`).

```yaml
search:
  backend: meilisearch
  meilisearch:
    host: http://localhost:7700
    api_key: your-api-key
    index_prefix: plexichat
```

* **Pros:** Very fast, typo tolerance, easy setup.
* **Cons:** Requires an external service, less flexible than Elasticsearch.

Select the backend with `SearchBackend` (`models.py:11`): `SQLITE_FTS5`, `ELASTICSEARCH`, `MEILISEARCH`.

## Server categories and verification

### Default categories

Configured in `search.discovery.categories`:

`gaming`, `music`, `entertainment`, `education`, `science`, `creative`, `social`, `sports`, `finance`, `other`. Category data is cached in `CappedDict` (`discovery/categories.py:23`) and read from `search_categories`.

### Verification levels

Defined in `models.py:33` and enforced in `discovery/verification.py:103`:

| Level | Requirement |
|-------|-------------|
| `none` | Default. |
| `low` | 10+ members. |
| `medium` | 100+ members. |
| `high` | 500+ members. |
| `verified` | 1000+ members, manual review. |

Use `verify_server` to set the level. `is_verified` is true only for `high` and `verified` (`discovery/verification.py:44`).

## Handle errors

Exceptions are defined in `exceptions.py` and re-exported by `__init__.py:34`:

| Exception | When it occurs |
|-----------|----------------|
| `SearchLimitError` | Limit exceeds `result_limit`. |
| `SearchPermissionError` | Permission check failed or servers module unavailable. |
| `InvalidQuerySyntaxError` | Query cannot be parsed. |
| `ServerNotListedError` | Server not in discovery (from `unlist_server` or search). |
| `BumpCooldownError` | Bump attempted before cooldown expires; includes `cooldown_remaining`. |
| `CategoryNotFoundError` | Unknown category. |
| `MinimumMembersError` | Server below `min_members_for_listing`. |

Example:

```python
from plexichat.src.core.search import SearchLimitError, SearchPermissionError
from plexichat.src.core.search import BumpCooldownError, ServerNotListedError

try:
    results = search.search_messages(user_id, query)
except SearchLimitError as e:
    print(f"Limit too high: max {e.max_allowed}")
except SearchPermissionError as e:
    print(f"Permission denied: {e.permission}")

try:
    search.bump_server(user_id, server_id)
except BumpCooldownError as e:
    print(f"Wait {e.cooldown_remaining // 1000} seconds")
except ServerNotListedError:
    print("Server not in discovery")
```

## Related reference

* **Database schema** (`schema.py`): `search_messages_fts`, `search_users_fts`, `search_servers_fts` (FTS5 virtual tables), `search_message_index`, `search_user_index`, `search_server_index`, `search_server_listings`, `search_categories`, `search_bump_history`, `search_history`.
* **Data models** (`models.py`): `MessageSearchResult`, `UserSearchResult`, `ServerSearchResult`, `ServerListing` (`models.py:182`), `ServerCategory`, `ParsedQuery`.
* **Tests:** `pytest src/tests/search/ -v`
