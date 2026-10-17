# Search indexers

This directory contains pluggable backends that index and search messages, users, and servers.

## Overview

Each backend implements the `BaseIndexer` abstract class defined in `base.py:33`. The manager creates one indexer at startup based on your configuration and database type. All backends expose the same interface, so you can switch backends without changing calling code.

`IndexerConfig` in `base.py:22` controls common behavior:

| Field | Default | Description |
|-------|---------|-------------|
| `batch_size` | `100` | Number of items to index in one batch |
| `write_time_indexing` | `true` | Index writes synchronously at write time |
| `result_limit` | `100` | Maximum results per query |
| `highlight_pre_tag` | `<mark>` | Opening tag for highlighted matches |
| `highlight_post_tag` | `</mark>` | Closing tag for highlighted matches |

## Backends

| Backend | File | When to use |
|---------|------|-------------|
| SQLite FTS5 | `sqlite_fts.py:58` | Default. No external dependencies. Uses three `fts5` virtual tables with `porter unicode61` tokenization. Validates schema at startup and requires a rebuild migration if legacy columns are detected. |
| Postgres | `postgres.py` | Automatically selected when `db.type == "postgres"` and backend is `sqlite_fts5`, or as the low-resource fallback. |
| Elasticsearch | `elasticsearch.py` | Remote full-text cluster. Configure hosts and index prefix under `search.elasticsearch`. |
| Meilisearch | `meilisearch.py` | Remote Meilisearch instance. Configure host and API key under `search.meilisearch`. |

SQLite FTS5 creates these tables in `sqlite_fts.py:84`:

* `search_messages_fts` — messages (content is indexed; IDs and metadata are `UNINDEXED`)
* `search_users_fts` — users
* `search_servers_fts` — servers

## Interface

`BaseIndexer` in `base.py:33` defines the contract that every backend implements:

* Lifecycle: `initialize()`, `close()`, `health_check()`, `get_stats()`
* Messages: `index_message()`, `index_messages_batch()`, `remove_message()`, `update_message()`, `search_messages()`, `search_messages_page()`
* Users: `index_user()`, `remove_user()`, `search_users()`, `search_users_page()`
* Servers: `index_server()`, `remove_server()`, `search_servers()`, `search_servers_page()`

Cursor pagination helpers `_encode_offset_cursor()` and `_decode_offset_cursor()` in `base.py:357` encode offsets as URL-safe base64 JSON with a `kind` discriminator (`msg`, `usr`, `srv`).

## Usage

Select a backend through configuration, then use the indexer directly if needed:

```python
from plexichat.src.core.search.indexer import SQLiteFTS5Indexer, IndexerConfig
from plexichat.src.core.search.models import IndexedMessage

config = IndexerConfig(batch_size=100, result_limit=50)
indexer = SQLiteFTS5Indexer(db, config)
indexer.initialize()

message = IndexedMessage(
    message_id=123,
    content="hello world",
    author_id=456,
    conversation_id=789,
)
indexer.index_message(message)

results = indexer.search_messages(query="hello", limit=25)
```

Build an FTS query manually only if you need to bypass the manager:

```python
# _build_fts_query in sqlite_fts.py:577 sanitizes special characters
# and converts terms to prefix queries: 'hello' -> '"hello"*'
fts_query = indexer._build_fts_query('hello "exact phrase"')
```

## Error handling

* `SearchIndexError` — a single document failed to index. Check `item_id`.
* `SearchBackendError` — the backend is unavailable or the query failed. Check `backend` and `original_error`.

The SQLite backend raises `SearchBackendError` during `initialize()` if an existing FTS table has an incompatible legacy schema. Drop the stale `search_*_fts` tables and reindex to resolve it.
