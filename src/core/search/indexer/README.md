# Search Indexers

Pluggable search backend implementations.

## Backends

- `sqlite_fts.py` - SQLite FTS5 (default, no external dependencies)
- `elasticsearch.py` - Elasticsearch adapter
- `meilisearch.py` - Meilisearch adapter

## Usage

```python
from src.core.search.indexer import SQLiteFTS5Indexer, ElasticsearchIndexer

indexer = SQLiteFTS5Indexer(config)
indexer.index_message(message)
results = indexer.search("query")
```

## Base Class

All indexers extend `BaseIndexer` which defines the interface for indexing and searching.
