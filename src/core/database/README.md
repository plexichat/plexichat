# Database Module

A database connectivity module supporting SQLite and PostgreSQL with a clean, consistent API.

## Features

- SQLite and PostgreSQL support
- Unified query syntax - use `?` placeholders for both databases (auto-converted to `%s` for PostgreSQL)
- Parameterized queries for SQL injection prevention
- Fetch helpers (fetch_one, fetch_all)
- Transaction support
- Context manager support
- Automatic directory creation for SQLite
- Dict-like row access for both SQLite and PostgreSQL
- Integrated logging
- **Connection pool monitoring** - Track pool health, acquisition times, and connection age

## Documentation

- [POOL_MONITORING.md](./POOL_MONITORING.md) - Comprehensive connection pool monitoring guide

## Requirements

```bash
pip install PyYAML  # For config (via common-utils)
pip install psycopg2-binary  # Only if using PostgreSQL
```

## Configuration

The database module reads configuration from the `database` key in your config file:

```yaml
database:
  type: sqlite  # or "postgres"
  path: data/app.db  # SQLite only
  
  # PostgreSQL settings (only used when type: postgres)
  postgres:
    host: localhost
    port: 5432
    user: postgres
    password: ""
    dbname: plexichat
  
  # Connection pool monitoring (PostgreSQL)
  connection_pool:
    min_connections: 2
    max_connections: 20
    connect_timeout: 10
    max_connection_age_hours: 0.5  # 30 minutes
  
  # Monitoring settings
  monitoring:
    log_interval_seconds: 60  # Log stats every 60 seconds
```

### Switching to PostgreSQL

1. Install the PostgreSQL driver:
   ```bash
   pip install psycopg2-binary
   ```

2. Update your config:
   ```yaml
   database:
     type: postgres
     postgres:
       host: localhost
       port: 5432
       user: postgres
       password: your_password
       dbname: plexichat
   ```

3. Ensure your PostgreSQL server is running and the database exists.

## Usage

### Basic Usage

```python
from core.database import Database

db = Database()
db.connect()

# Create table
db.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT, email TEXT)")

# Insert data - use ? placeholders (works for both SQLite and PostgreSQL)
db.execute("INSERT INTO users (name, email) VALUES (?, ?)", ("Alice", "alice@example.com"))

# Fetch single row - returns dict-like object
user = db.fetch_one("SELECT * FROM users WHERE id = ?", (1,))
print(user["name"])  # Alice

# Fetch all rows
users = db.fetch_all("SELECT * FROM users")
for user in users:
    print(user["name"])

db.close()
```

### Cross-Database Compatibility

The module automatically converts `?` placeholders to `%s` for PostgreSQL, so you can write database-agnostic code:

```python
# This works for both SQLite and PostgreSQL
db.execute("INSERT INTO users (name) VALUES (?)", ("Bob",))
db.fetch_one("SELECT * FROM users WHERE name = ?", ("Bob",))
```

### Connection Pool Monitoring

Monitor and track database connection pool health:

```python
from core.database import Database

db = Database()

# Start automatic periodic logging of pool stats
db.start_pool_monitoring()

# Get current pool statistics
stats = db.get_pool_stats()
print(f"Active connections: {stats['active_connections']}")
print(f"Pool utilization: {(stats['active_connections'] / stats['max_connections'] * 100):.1f}%")
print(f"Avg acquisition time: {stats['avg_acquisition_time']:.3f}s")

# Stop monitoring when done
db.stop_pool_monitoring()
```

For comprehensive monitoring guide, see [POOL_MONITORING.md](./POOL_MONITORING.md)

### Context Manager

```python
from core.database import Database

with Database() as db:
    db.execute("INSERT INTO users (name) VALUES (?)", ("Bob",))
    users = db.fetch_all("SELECT * FROM users")
# Connection automatically closed
```

### Batch Operations

```python
db.execute_many(
    "INSERT INTO users (name, email) VALUES (?, ?)",
    [
        ("Alice", "alice@example.com"),
        ("Bob", "bob@example.com"),
        ("Charlie", "charlie@example.com"),
    ]
)
```

### Transactions

```python
db.begin_transaction()
try:
    db.execute("INSERT INTO accounts (user_id, balance) VALUES (?, ?)", (1, 100))
    db.execute("UPDATE accounts SET balance = balance - 50 WHERE user_id = ?", (1,))
    db.commit()
except Exception:
    db.rollback()
    raise
```

### Check Table Existence

```python
if not db.table_exists("users"):
    db.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")
```

## API Reference

### Database Class

#### `__init__()`
Initialize the database manager. Reads configuration from config module.

#### `connect()`
Establish connection to the database.

#### `execute(query, params=None)`
Execute a query with optional parameters. Returns cursor.

#### `execute_many(query, params_list)`
Execute a query multiple times with different parameters.

#### `fetch_one(query, params=None)`
Execute query and return single result or None.

#### `fetch_all(query, params=None)`
Execute query and return all results as list.

#### `table_exists(table_name)`
Check if a table exists. Returns boolean.

#### `begin_transaction()`
Start a transaction.

#### `commit()`
Commit current transaction.

#### `rollback()`
Rollback current transaction.

#### `close()`
Close the database connection.

#### `insert_or_ignore(table, columns, values)`
Insert a row if it doesn't already exist. Cross-database compatible alternative to SQLite's `INSERT OR IGNORE`.

#### `upsert(table, columns, values, conflict_columns, update_columns=None)`
Insert a row or update it if it already exists. Cross-database compatible alternative to SQLite's `INSERT OR REPLACE`.

### Cross-Database Upsert Operations

For operations that need to handle conflicts (insert-or-ignore, insert-or-update), use the helper methods instead of raw SQL:

```python
# Insert if not exists (ignores duplicates)
db.insert_or_ignore("users", ["id", "name"], (1, "alice"))

# Insert or update (upsert)
db.upsert(
    table="users",
    columns=["id", "name", "email"],
    values=(1, "alice", "alice@example.com"),
    conflict_columns=["id"],  # Columns that define uniqueness
    update_columns=["name", "email"]  # Columns to update on conflict
)
```

These methods automatically generate the correct SQL for each database:
- SQLite: `INSERT OR IGNORE` / `INSERT OR REPLACE`
- PostgreSQL: `INSERT ... ON CONFLICT DO NOTHING` / `INSERT ... ON CONFLICT DO UPDATE`

## Error Handling

The module raises appropriate exceptions:

- `ValueError`: Invalid configuration or unsupported database type
- `ConnectionError`: Operations attempted without connection
- `ImportError`: psycopg2 not installed when using PostgreSQL
- `sqlite3.Error`: SQLite-specific errors
- `psycopg2.Error`: PostgreSQL-specific errors

All errors are logged before being raised.

## Thread Safety

SQLite connections are not thread-safe by default. For multi-threaded applications:
- Create a new Database instance per thread, or
- Use connection pooling (future enhancement)

## PostgreSQL Notes

- Uses `psycopg2-binary` driver (sync, not async)
- Uses `RealDictCursor` for dict-like row access matching SQLite behavior
- Autocommit is disabled by default to match SQLite transaction behavior
- The `?` placeholder syntax is automatically converted to `%s`
- Supports `sslmode` configuration (default: `prefer`)

### PostgreSQL Configuration Options

```yaml
database:
  type: postgres
  postgres:
    host: localhost
    port: 5432
    user: postgres
    password: your_password
    dbname: plexichat
    sslmode: prefer  # disable, allow, prefer, require, verify-ca, verify-full
```

### PostgreSQL Compatibility Status

All core modules have been migrated to use cross-database compatible methods:

- `voice/manager.py` - Channel settings and AFK settings
- `search/manager.py` - Message, user, and server indexing
- `search/schema.py` - Category seeding
- `relationships/manager.py` - Friend creation
- `presence/manager.py` - Presence, custom status, activity, and typing indicators

The codebase is now fully compatible with both SQLite and PostgreSQL.

### Writing Cross-Database Code

For new code, always use the helper methods instead of SQLite-specific syntax:

| SQLite Syntax | Cross-Database Method |
|---------------|----------------------|
| `INSERT OR IGNORE` | `db.insert_or_ignore()` |
| `INSERT OR REPLACE` | `db.upsert()` |

Note: `AUTOINCREMENT` should be avoided; PostgreSQL uses `SERIAL` or `GENERATED AS IDENTITY`.

## Testing

```bash
pytest src/tests/test_database.py -v
```

PostgreSQL tests are skipped if no PostgreSQL server is available.


---

# Redis Module

A Redis client module for caching, sessions, presence, and pub/sub with connection pooling and graceful degradation.

## Features

- Connection pooling with automatic reconnection
- TLS/SSL support for secure connections
- Key prefixing to avoid collisions
- Graceful degradation when Redis is unavailable
- Pub/Sub support for real-time events
- Decorator-based caching (`@cached`)
- Session and presence caching helpers
- Rate limiting support
- Health checks and monitoring

## Requirements

```bash
pip install redis>=5.0.0      # Redis client
pip install hiredis>=2.3.0    # Optional, for performance
pip install fakeredis>=2.20   # For testing (optional)
```

## Configuration

Add to your `config.yaml`:

```yaml
redis:
  enabled: true
  host: localhost
  port: 6379
  password: ""              # Leave empty for no auth
  db: 0
  ssl: false
  ssl_cert_reqs: required   # required, optional, or none
  ssl_ca_certs: ""          # Path to CA cert file
  connection_pool:
    max_connections: 50
    timeout: 5
  key_prefix: "plexichat:"
  ttl:
    session: 1800           # 30 minutes
    presence: 300           # 5 minutes
    cache: 60               # 1 minute default
```

### Environment Variables

| Variable | Description |
|----------|-------------|
| `REDIS_URL` | Full Redis URL (overrides config) |
| `REDIS_HOST` | Redis host |
| `REDIS_PORT` | Redis port |
| `REDIS_PASSWORD` | Redis password |

## Usage

### Basic Redis Client

```python
from src.core.database import RedisClient

client = RedisClient()
client.connect()

# Basic operations
client.set("key", "value", ttl=300)
value = client.get("key")

# JSON operations
client.set_json("user:1", {"name": "Alice", "age": 30})
user = client.get_json("user:1")

# Hash operations
client.hset("user:1", "name", "Alice")
client.hset("user:1", "email", "alice@example.com")
all_fields = client.hgetall("user:1")

# List operations
client.rpush("queue", "item1", "item2")
item = client.lpop("queue")

# Set operations
client.sadd("tags", "python", "redis")
is_member = client.sismember("tags", "python")

# Counter operations
client.incr("page_views")
client.decr("stock", 5)

client.close()
```

### Context Manager

```python
with RedisClient() as client:
    client.set("key", "value")
    value = client.get("key")
# Connection automatically closed
```

### Module-Level Setup

```python
from src.core.database import setup_redis, get_redis_client, redis_available

# Setup once in main.py
setup_redis()

# Use anywhere
if redis_available():
    client = get_redis_client()
    client.set("key", "value")
```

### Caching Decorator

```python
from src.core.database import cached

@cached(ttl=300)
def get_user(user_id: int) -> dict:
    # Expensive database query
    return db.fetch_one("SELECT * FROM users WHERE id = ?", (user_id,))

# First call executes function, caches result
user = get_user(1)

# Second call returns cached result
user = get_user(1)

# Invalidate cache
get_user.invalidate(1)
```

### Manual Cache Operations

```python
from src.core.database import cache_get, cache_set, cache_delete, invalidate_pattern

# Set/get
cache_set("user:1:profile", {"name": "Alice"}, ttl=300)
profile = cache_get("user:1:profile")

# Delete
cache_delete("user:1:profile")

# Pattern invalidation
invalidate_pattern("user:1:*")  # Deletes all user:1:* keys
```

### Session Caching

```python
from src.core.database import (
    cache_session,
    get_cached_session,
    invalidate_session,
    invalidate_user_sessions,
)

# Cache a session
cache_session("sess_abc123", user_id=1, data={"ip": "127.0.0.1"})

# Get session
session = get_cached_session("sess_abc123")

# Invalidate single session
invalidate_session("sess_abc123", user_id=1)

# Invalidate all user sessions (logout everywhere)
invalidate_user_sessions(user_id=1)
```

### Presence Caching

```python
from src.core.database import (
    cache_presence,
    get_cached_presence,
    get_bulk_presence,
)

# Cache presence
cache_presence(user_id=1, status="online", custom_status="Playing games")

# Get single presence
presence = get_cached_presence(1)

# Get multiple presences
presences = get_bulk_presence([1, 2, 3, 4, 5])
```

### Rate Limiting

```python
from src.core.database import check_rate_limit, reset_rate_limit

# Check rate limit (5 requests per 60 seconds)
allowed, remaining = check_rate_limit("user:1:api", limit=5, window_seconds=60)

if not allowed:
    raise RateLimitExceeded(f"Rate limit exceeded. Try again later.")

# Reset rate limit
reset_rate_limit("user:1:api")
```

### Pub/Sub

```python
from src.core.database import RedisClient

client = RedisClient()
client.connect()

# Publish
client.publish("events", '{"type": "message", "data": "Hello"}')

# Subscribe
pubsub = client.subscribe("events", "notifications")
for message in pubsub.listen():
    if message["type"] == "message":
        print(f"Received: {message['data']}")
```

### Health Check

```python
from src.core.database import cache_health

health = cache_health()
print(health)
# {
#     "available": True,
#     "stats": {"hits": 150, "misses": 30, "errors": 0},
#     "hit_rate": 83.33,
#     "redis": {
#         "enabled": True,
#         "connected": True,
#         "responsive": True,
#         "latency_ms": 0.45
#     }
# }
```

## Security

### Authentication

Set a password in Redis and config:

```yaml
redis:
  password: "your-secure-password"
```

### TLS/SSL

For encrypted connections:

```yaml
redis:
  ssl: true
  ssl_cert_reqs: required
  ssl_ca_certs: /path/to/ca-cert.pem
```

### Key Prefixing

All keys are automatically prefixed to avoid collisions:

```yaml
redis:
  key_prefix: "plexichat:"
```

A key `user:1` becomes `plexichat:user:1` in Redis.

### Input Sanitization

Keys are automatically sanitized to prevent injection:
- Control characters removed
- Length limited to 512 characters
- No sensitive data should be stored in keys (use IDs only)

## Error Handling

```python
from src.core.database import (
    RedisError,
    RedisConnectionError,
    RedisOperationError,
)

try:
    client.connect()
except RedisConnectionError as e:
    logger.error(f"Redis unavailable: {e}")
    # Fall back to database-only mode

try:
    client.set("key", "value")
except RedisOperationError as e:
    logger.error(f"Redis operation failed: {e}")
```

## Graceful Degradation

The cache module gracefully handles Redis unavailability:

```python
from src.core.database import cached, redis_available

@cached(ttl=300)
def get_user(user_id: int) -> dict:
    return db.fetch_one("SELECT * FROM users WHERE id = ?", (user_id,))

# If Redis is down, function executes normally without caching
user = get_user(1)

# Check availability
if redis_available():
    # Redis-specific operations
    pass
```

## Testing

```bash
# Install test dependencies
pip install fakeredis>=2.20

# Run tests
pytest src/tests/test_redis.py -v
```

Tests use `fakeredis` for unit tests (no real Redis required). Integration tests with real Redis are skipped if Redis is unavailable.


---

# RAM Cache Module

In-memory caching for small, frequently-read reference tables that rarely change.

## When to Use RAM Cache vs Redis

| Use Case | RAM Cache | Redis |
|----------|-----------|-------|
| Static reference data (categories, config) | OK | |
| User-specific data (settings, presence) | | OK |
| Session data | | OK |
| Data shared across server instances | | OK |
| Data that changes frequently | | OK |
| Tiny tables (< 100 rows) read 1000x/sec | OK | |

## Usage

```python
from src.core.database import RAMCache, create_cache, get_cache

# Create a cache for categories (1 hour TTL)
cache = create_cache("categories", ttl=3600)

# Load data from database
cache.load(db, "SELECT * FROM search_categories ORDER BY position")

# Get all items
all_categories = cache.get_all()

# Get by field value
gaming = cache.get("id", "gaming")

# Get multiple by field
selected = cache.get_many("id", ["gaming", "music", "tech"])

# Filter with predicate
popular = cache.filter(lambda x: x["server_count"] > 100)

# Invalidate on write
cache.invalidate()
```

## Thread Safety

The RAM cache is thread-safe using `threading.RLock`. Multiple threads can safely read and write concurrently.

## Auto-Reload

If the cache expires and has a stored query, it will automatically reload from the database on the next access.

---

# Caching Strategy

Plexichat uses a multi-tier caching strategy:

## Tier 1: RAM Cache (In-Process)
- **TTL**: 1 hour
- **Data**: Static reference tables (search_categories)
- **Latency**: ~1 nanosecond

## Tier 2: Redis Cache (External)
- **TTL**: 30 seconds to 10 minutes depending on data type
- **Data**: User data, server info, notification settings, sessions, presence
- **Latency**: ~0.5-2ms

## Tier 3: Database (PostgreSQL)
- **TTL**: Permanent
- **Data**: All persistent data
- **Latency**: ~5-50ms

## Cache Keys

| Data Type | Key Pattern | TTL |
|-----------|-------------|-----|
| User profile | `user:{user_id}` | 60s |
| Server info | `server:{server_id}` | 300s |
| Notification settings | `notif_settings:{user_id}:{server_id}` | 600s |
| Session | `session:{session_id}` | 1800s |
| Presence | `presence:{user_id}` | 300s |
| Token verification | `token:{hash}` | 30s |

## Cache Invalidation

Caches are invalidated on writes:
- User update -> invalidate `user:*{user_id}*`
- Server update -> invalidate `server:{server_id}`
- Settings update -> invalidate `notif_settings:{user_id}:*`

## Graceful Degradation

If Redis is unavailable, all operations fall back to direct database queries. The application continues to function, just with higher latency.
