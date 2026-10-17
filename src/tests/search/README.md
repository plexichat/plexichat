# Search tests

This directory contains tests for the Plexichat search subsystem. Tests cover
indexing, querying, ranking, permissions, and discovery across all supported
backends.

## Test files

| File | Description |
| --- | --- |
| `test_discovery.py` | Server discovery (public listings, bump, verify) |
| `test_edge_cases.py` | Edge case handling |
| `test_filters.py` | Search filter logic |
| `test_indexer_elasticsearch.py` | Elasticsearch backend |
| `test_indexer_meilisearch.py` | Meilisearch backend |
| `test_indexer_sqlite.py` | SQLite FTS backend |
| `test_message_search.py` | Message search |
| `test_permissions.py` | Search permission checks |
| `test_query_parser.py` | Query parsing |
| `test_ranking.py` | Result ranking |
| `test_server_search.py` | Server search |
| `test_user_search.py` | User search |

## Running the tests

Run all search tests:

```bash
pytest src/tests/search/ -v
```

Run a single test file:

```bash
pytest src/tests/search/test_filters.py -v
```

Run tests marked as integration:

```bash
pytest src/tests/search/ -m integration -v
```

## Fixtures

Shared fixtures are defined in `conftest.py`. Key fixtures include:

- **`users_with_dm`** -- Creates two users with a DM conversation and indexed
  messages. Returns `(user1, user2, dm, messages, search)`.
- **`users_with_server`** -- Creates an owner and two members in a server.
  Returns `(owner, member1, member2, server, server_manager, search)`.
- **`indexed_users`** -- Creates five users and indexes them for user search
  tests. Returns `(users, search)`.
