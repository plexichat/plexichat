# Search & Discovery Module

Full-text search and server discovery system for PlexiChat supporting advanced query syntax, multiple search backends, and public server directory.

## Features

- Full-text message search with advanced query syntax
- User search by username/display name
- Server search by name/description/tags
- Public server directory with categories
- Server verification system
- Multiple indexer backends (SQLite FTS5, Elasticsearch, Meilisearch)
- Incremental index updates on message create/edit/delete
- Permission-aware search (users only see messages they can access)
- Search result ranking by relevance

## Setup

```python
from src.core.database import Database
from src.core import auth, messaging, servers, search

# Initialize database
db = Database()
db.connect()

# Initialize dependencies
auth.setup(db)
messaging.setup(db, auth)
servers.setup(db, auth, messaging)

# Initialize search
search.setup(db, auth, messaging, servers)
```

## Usage

### Message Search

```python
from src.core import search

# Basic search
results = search.search_messages(user_id=1, query="hello world")

# Search with filters
results = search.search_messages(
    user_id=1,
    query='from:alice "exact phrase" has:image'
)

# Search within a server
results = search.search_messages(
    user_id=1,
    query="meeting notes",
    server_id=123
)

# Search within a channel
results = search.search_messages(
    user_id=1,
    query="bug report",
    channel_id=456
)
```

### Query Syntax

| Filter | Description | Example |
|--------|-------------|---------|
| `from:user` | Messages from a user | `from:alice` |
| `in:channel` | Messages in a channel | `in:general` |
| `before:date` | Messages before date | `before:2024-01-01` |
| `after:date` | Messages after date | `after:7d` (7 days ago) |
| `has:type` | Messages with attachments | `has:image`, `has:link`, `has:file` |
| `mentions:user` | Messages mentioning user | `mentions:bob` |
| `pinned:true` | Pinned messages | `pinned:true` |
| `"phrase"` | Exact phrase match | `"hello world"` |
| `-filter:value` | Negated filter | `-from:bot` |

Date formats supported:
- ISO: `2024-01-15`
- Relative: `7d` (days), `2w` (weeks), `1m` (months), `1y` (years)
- Keywords: `today`, `yesterday`

### User Search

```python
# Search all users
results = search.search_users(user_id=1, query="alice")

# Search within a server
results = search.search_users(
    user_id=1,
    query="mod",
    server_id=123
)
```

### Server Search

```python
# Search public servers
results = search.search_servers(user_id=1, query="gaming")

# Search by category
results = search.search_servers(
    user_id=1,
    query="minecraft",
    category="gaming"
)
```

### Server Discovery

```python
# List public servers
servers = search.list_public_servers(
    category="gaming",
    sort_by="member_count",  # or "bumped_at", "created_at"
    limit=25
)

# Get categories
categories = search.get_server_categories()

# List your server
listing = search.list_server(
    user_id=owner_id,
    server_id=123,
    category="gaming",
    description="A friendly gaming community",
    tags=["minecraft", "survival", "friendly"]
)

# Bump your server
search.bump_server(user_id=1, server_id=123)

# Unlist server
search.unlist_server(user_id=owner_id, server_id=123)
```

### Indexing

Messages are automatically indexed when `write_time_indexing` is enabled. For manual indexing:

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
    }
)

# Remove from index
search.remove_from_index(message_id=123)

# Reindex a conversation
search.reindex_conversation(conversation_id=456)
```

### Query Parsing

```python
# Parse a query
parsed = search.parse_query('from:alice hello world has:image')

print(parsed.search_terms)  # ['hello', 'world']
print(parsed.filters)  # [QueryFilter(FROM_USER, 'alice'), QueryFilter(HAS_ATTACHMENT, 'image')]

# Get suggestions
suggestions = search.get_search_suggestions(user_id=1, partial_query="from:")
```

## Configuration

Add to `config/config.yaml`:

```yaml
search:
  # Backend: sqlite_fts5 (default), elasticsearch, meilisearch
  backend: sqlite_fts5
  
  # Indexing settings
  batch_size: 100
  write_time_indexing: true
  result_limit: 100
  
  # Elasticsearch settings (if using elasticsearch backend)
  elasticsearch:
    hosts:
      - http://localhost:9200
    index_prefix: plexichat
  
  # Meilisearch settings (if using meilisearch backend)
  meilisearch:
    host: http://localhost:7700
    api_key: null
    index_prefix: plexichat
  
  # Discovery settings
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

## Search Backends

### SQLite FTS5 (Default)

Works out of the box with no external dependencies. Uses SQLite's built-in FTS5 extension with Porter stemming.

Pros:
- Zero configuration
- No external services
- Good for small to medium deployments

Cons:
- Limited scalability
- Basic relevance ranking

### Elasticsearch

Production-grade search for large deployments.

```yaml
search:
  backend: elasticsearch
  elasticsearch:
    hosts:
      - http://localhost:9200
      - http://localhost:9201
    index_prefix: plexichat
```

Pros:
- Highly scalable
- Advanced relevance tuning
- Rich query DSL

Cons:
- Requires external service
- More complex setup

### Meilisearch

Fast, typo-tolerant search engine.

```yaml
search:
  backend: meilisearch
  meilisearch:
    host: http://localhost:7700
    api_key: your-api-key
    index_prefix: plexichat
```

Pros:
- Very fast
- Typo tolerance
- Easy setup

Cons:
- Requires external service
- Less flexible than Elasticsearch

## Server Categories

Default categories:
- `gaming` - Gaming communities
- `music` - Music discussion and sharing
- `entertainment` - Movies, TV, anime
- `education` - Learning and academics
- `science` - Science and technology
- `creative` - Art, design, writing
- `social` - General hangout
- `sports` - Sports communities
- `finance` - Investing and trading
- `other` - Everything else

## Verification Levels

| Level | Requirements |
|-------|-------------|
| `none` | Default |
| `low` | 10+ members |
| `medium` | 100+ members |
| `high` | 500+ members |
| `verified` | 1000+ members, manual review |

## Error Handling

```python
from src.core.search import (
    SearchError,
    SearchNotFoundError,
    SearchPermissionError,
    SearchQueryError,
    InvalidQuerySyntaxError,
    SearchLimitError,
    DiscoveryError,
    ServerNotListedError,
    BumpCooldownError,
    CategoryNotFoundError,
    MinimumMembersError,
)

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

## Database Schema

Tables (prefixed with `search_`):
- `search_messages_fts` - FTS5 virtual table for messages
- `search_users_fts` - FTS5 virtual table for users
- `search_servers_fts` - FTS5 virtual table for servers
- `search_message_index` - Message index metadata
- `search_user_index` - User index metadata
- `search_server_index` - Server index metadata
- `search_server_listings` - Discovery listings
- `search_categories` - Server categories
- `search_bump_history` - Bump tracking
- `search_history` - Search history for suggestions

## Testing

```bash
pytest src/tests/search/ -v
```

## Integration with Messaging

To enable automatic indexing, integrate with the messaging module:

```python
# In messaging module after sending a message
search.index_message(
    message_id=msg.id,
    content=msg.content,
    metadata={
        "author_id": msg.author_id,
        "conversation_id": msg.conversation_id,
        "created_at": msg.created_at,
    }
)

# After editing a message
search.index_message(msg.id, msg.content, metadata)

# After deleting a message
search.remove_from_index(msg.id)
```
