# Database module

## Overview

The database module provides a unified interface for SQLite and PostgreSQL. It supports parameterized queries, transactions, connection-pool monitoring, and cross-database helpers. Use `?` placeholders in all queries; the module converts them to `%s` for PostgreSQL automatically.

Related platform endpoints:

*   WebSocket gateway: `/gateway` — see `plexichat/src/api/websocket/gateway.py:261`. Use `/gateway`, not `/ws`.
*   Readiness probe: `GET /api/v1/ready` — see `plexichat/src/api/routes/health.py:18`.
*   Deep health check: `GET /api/v1/health` — see `plexichat/src/api/routes/health.py:46` with root alias at `GET /health` — see `plexichat/src/api/app.py:574`.

For gateway opcodes and intents, see `plexichat/src/api/websocket/opcodes.py:40` and `plexichat/src/core/events/types.py:120`.

## Features

*   SQLite and PostgreSQL support through a single API.
*   Unified `?` placeholder syntax with automatic conversion for PostgreSQL.
*   Parameterized queries for SQL injection prevention.
*   Fetch helpers: `fetch_one` and `fetch_all`.
*   Transaction support with savepoints.
*   Context-manager support.
*   Automatic directory creation for SQLite.
*   Dict-like row access for both backends.
*   Integrated logging.
*   Connection-pool monitoring for health, acquisition time, and connection age.

## Documentation

*   `POOL_MONITORING.md` — connection-pool monitoring guide.

## Requirements

```bash
pip install PyYAML  # For config through common_utils
pip install pg8000  # Only if you use PostgreSQL
```

## Configuration

The module reads the `database` key — see `plexichat/src/config_defaults.py:64`:

```yaml
database:
  type: sqlite  # or "postgres"
  path: data/app.db  # SQLite only

  # PostgreSQL settings (only when type is "postgres")
  postgres:
    host: localhost
    port: 5432
    user: postgres
    password: ""
    dbname: plexichat
    sslmode: prefer

  # Connection pool (PostgreSQL and SQLite)
  connection_pool:
    min_connections: 5
    max_connections: 100
    connect_timeout: 10
    max_connection_age_hours: 2.0

  # Monitoring
  monitoring:
    slow_query_threshold_ms: 1000
    alert_on_slow_queries: true

  migrations:
    auto_migrate: true
```

Global server keys use these paths — see `plexichat/src/config_defaults.py`:

*   `server.host`, `server.port`, `server.reload` — see `plexichat/src/config_defaults.py:54`. Do not use `api.host`, `api.port`, or `api.workers`.
*   `api.api_prefix` — default `/api/v1` — see `plexichat/src/config_defaults.py:131`. Do not use `api.prefix` or `prefix`.
*   `tls` — see `plexichat/src/config_defaults.py:599`. Do not use `api.ssl`.

### Switch to PostgreSQL

1.  Install the driver:

    ```bash
    pip install pg8000
    ```

2.  Update your config:

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

3.  Ensure the PostgreSQL server is running and the database exists.

## Usage

### Basic usage

```python
from src.core.database import Database

db = Database()
db.connect()

# Create a table
db.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT, email TEXT)")

# Insert data — use ? placeholders for both SQLite and PostgreSQL
db.execute("INSERT INTO users (name, email) VALUES (?, ?)", ("Alice", "alice@example.com"))

# Fetch one row — returns a dict-like object
user = db.fetch_one("SELECT * FROM users WHERE id = ?", (1,))
print(user["name"])  # Alice

# Fetch all rows
users = db.fetch_all("SELECT * FROM users")
for user in users:
    print(user["name"])

db.close()
```

### Cross-database compatibility

The module converts `?` to `%s` for PostgreSQL, so you can write database-agnostic code — see `plexichat/src/core/database/core/compat.py`:

```python
# Works for both SQLite and PostgreSQL
db.execute("INSERT INTO users (name) VALUES (?)", ("Bob",))
db.fetch_one("SELECT * FROM users WHERE name = ?", ("Bob",))
```

### Connection-pool monitoring

```python
from src.core.database import Database

db = Database()

# Start periodic logging of pool stats
db.start_pool_monitoring()

# Get current pool statistics
stats = db.get_pool_stats()
print(f"Active connections: {stats['active_connections']}")
print(f"Pool utilization: {(stats['active_connections'] / stats['max_connections'] * 100):.1f}%")
print(f"Avg acquisition time: {stats['avg_acquisition_time']:.3f}s")

# Stop monitoring when done
db.stop_pool_monitoring()
```

For details, see `POOL_MONITORING.md`.

### Context manager

```python
from src.core.database import Database

with Database() as db:
    db.execute("INSERT INTO users (name) VALUES (?)", ("Bob",))
    users = db.fetch_all("SELECT * FROM users")
# Connection is closed automatically.
```

### Batch operations

```python
db.execute_many(
    "INSERT INTO users (name, email) VALUES (?, ?)",
    [
        ("Alice", "alice@example.com"),
        ("Bob", "bob@example.com"),
        ("Charlie", "charlie@example.com"),
    ],
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

### Check table existence

```python
if not db.table_exists("users"):
    db.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")
```

## API reference

### `Database` class — see `plexichat/src/core/database/core/manager.py:29`

| Method | Description |
| --- | --- |
| `__init__()` | Initialize the manager. Reads config from `database`. |
| `connect()` | Establish a connection. |
| `execute(query, params=None)` | Execute a query with optional parameters. Returns a cursor. |
| `execute_many(query, params_list)` | Execute a query multiple times. |
| `fetch_one(query, params=None)` | Execute a query and return one result or `None`. |
| `fetch_all(query, params=None)` | Execute a query and return all results. |
| `table_exists(table_name)` | Check whether a table exists. Returns `bool`. |
| `begin_transaction()` | Start a transaction. |
| `commit()` | Commit the current transaction. |
| `rollback()` | Roll back the current transaction. |
| `close()` | Close the connection. |
| `insert_or_ignore(table, columns, values)` | Cross-database `INSERT OR IGNORE` helper. |
| `upsert(table, columns, values, conflict_columns, update_columns=None)` | Cross-database upsert helper. |

### Cross-database upsert helpers

Use these helpers instead of raw `INSERT OR IGNORE` or `INSERT OR REPLACE`:

```python
# Insert if not exists
db.insert_or_ignore("users", ["id", "name"], (1, "alice"))

# Insert or update
db.upsert(
    table="users",
    columns=["id", "name", "email"],
    values=(1, "alice", "alice@example.com"),
    conflict_columns=["id"],
    update_columns=["name", "email"],
)
```

Generated SQL:

*   SQLite: `INSERT OR IGNORE` / `INSERT OR REPLACE`
*   PostgreSQL: `INSERT ... ON CONFLICT DO NOTHING` / `INSERT ... ON CONFLICT DO UPDATE`

## Error handling

| Exception | Cause |
| --- | --- |
| `ValueError` | Invalid configuration or unsupported database type. |
| `ConnectionError` | Operation attempted without a connection. |
| `ImportError` | `pg8000` not installed when `type` is `postgres`. |
| `sqlite3.Error` | SQLite-specific error. |
| `pg8000.dbapi.DatabaseError` | PostgreSQL-specific error (`IntegrityError`, `OperationalError`, ...). |
| `TimeoutError` | Pool had no connection available within `connect_timeout`. |

All errors are logged before they are raised.

## Thread safety

SQLite connections are not thread-safe by default. For multi-threaded use, create one `Database` instance per thread or use connection pooling.

## PostgreSQL notes

*   Uses the `pg8000` driver (synchronous, BSD-3-Clause), installed as `pg8000`.
*   Rows are tuples; the execution layer rebuilds dicts from `cursor.description`, matching SQLite behavior.
*   Autocommit is disabled to match SQLite transaction semantics.
*   Connection pooling is in-tree (`engines/pool.py`), shared with the SQLite engine.
*   `?` placeholders are converted to `%s` automatically.
*   Supports `sslmode` — default `prefer` — see `plexichat/src/config_defaults.py:79`.

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

### Compatibility status

All core modules use the cross-database helpers. For new code, use the helpers instead of SQLite-specific syntax:

| SQLite syntax | Cross-database method |
| --- | --- |
| `INSERT OR IGNORE` | `db.insert_or_ignore()` |
| `INSERT OR REPLACE` | `db.upsert()` |

Avoid `AUTOINCREMENT`; PostgreSQL uses `SERIAL` or `GENERATED AS IDENTITY`.

## Testing

```bash
pytest src/tests/test_database.py -v
```

PostgreSQL tests are skipped when no PostgreSQL server is available.

---

# Valkey module

Valkey provides caching, sessions, presence, and pub/sub with pooling and graceful degradation — see `plexichat/src/config_defaults.py:111`.

## Features

*   Connection pooling with automatic reconnection.
*   TLS/SSL support.
*   Key prefixing to avoid collisions.
*   Graceful degradation when Valkey is unavailable.
*   Pub/sub for real-time events.
*   Decorator-based caching (`@cached`).
*   Session and presence helpers.
*   Rate limiting.
*   Health checks and monitoring.

## Requirements

```bash
pip install valkey-glide-sync>=2.5.0
```

## Configuration

Add to your `config.yaml` — see `plexichat/src/config_defaults.py:111`:

```yaml
valkey:
  enabled: true
  host: localhost
  port: 6379
  password: ""              # Leave empty for no auth
  db: 0
  ssl: false
  ssl_cert_reqs: required   # required, optional, or none
  ssl_ca_certs: ""          # Path to CA cert file
  key_prefix: "plexichat:"
  ttl:
    session: 1800           # 30 minutes
    presence: 300           # 5 minutes
    cache: 60               # 1 minute default
```

### Environment variables

| Variable | Description |
| --- | --- |
| `VALKEY_URL` | Full Valkey URL (overrides config). |
| `VALKEY_HOST` | Valkey host. |
| `VALKEY_PORT` | Valkey port. |
| `VALKEY_PASSWORD` | Valkey password. |

## Usage

### Basic Valkey client

```python
from src.core.database import ValkeyClient

client = ValkeyClient()
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

### Context manager

```python
with ValkeyClient() as client:
    client.set("key", "value")
    value = client.get("key")
# Connection is closed automatically.
```

### Module-level setup

```python
from src.core.database import setup_valkey, get_valkey_client, valkey_available

# Setup once in main.py
setup_valkey()

# Use anywhere
if valkey_available():
    client = get_valkey_client()
    client.set("key", "value")
```

### Caching decorator

```python
from src.core.database import cached

@cached(ttl=300)
def get_user(user_id: int) -> dict:
    return db.fetch_one("SELECT * FROM users WHERE id = ?", (user_id,))

# First call executes and caches
user = get_user(1)

# Second call returns the cached result
user = get_user(1)

# Invalidate
get_user.invalidate(1)
```

### Manual cache operations

```python
from src.core.database import cache_get, cache_set, cache_delete, invalidate_pattern

# Set and get
cache_set("user:1:profile", {"name": "Alice"}, ttl=300)
profile = cache_get("user:1:profile")

# Delete
cache_delete("user:1:profile")

# Pattern invalidation
invalidate_pattern("user:1:*")  # Deletes all user:1:* keys
```

### Session caching

```python
from src.core.database import (
    cache_session,
    get_cached_session,
    invalidate_session,
    invalidate_user_sessions,
)

# Cache a session
cache_session("sess_abc123", user_id=1, data={"ip": "127.0.0.1"})

# Get a session
session = get_cached_session("sess_abc123")

# Invalidate one session
invalidate_session("sess_abc123", user_id=1)

# Invalidate all sessions for a user (logout everywhere)
invalidate_user_sessions(user_id=1)
```

### Presence caching

```python
from src.core.database import (
    cache_presence,
    get_cached_presence,
    get_bulk_presence,
)

# Cache presence
cache_presence(user_id=1, status="online", custom_status="Playing games")

# Get one presence
presence = get_cached_presence(1)

# Get multiple presences
presences = get_bulk_presence([1, 2, 3, 4, 5])
```

### Rate limiting

```python
from src.core.database import check_rate_limit, reset_rate_limit

# Check rate limit (5 requests per 60 seconds)
allowed, remaining = check_rate_limit("user:1:api", limit=5, window_seconds=60)

if not allowed:
    raise RateLimitExceeded("Rate limit exceeded. Try again later.")

# Reset
reset_rate_limit("user:1:api")
```

### Pub/sub

```python
from src.core.database import ValkeyClient

client = ValkeyClient()
client.connect()

# Publish
client.publish("events", '{"type": "message", "data": "Hello"}')

# Subscribe
pubsub = client.subscribe("events", "notifications")
for message in pubsub.listen():
    if message["type"] == "message":
        print(f"Received: {message['data']}")
```

### Health check

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

```yaml
valkey:
  password: "your-secure-password"
```

### TLS/SSL

```yaml
valkey:
  ssl: true
  ssl_cert_reqs: required
  ssl_ca_certs: /path/to/ca-cert.pem
```

### Key prefixing

All keys are prefixed automatically — see `plexichat/src/config_defaults.py:118`:

```yaml
valkey:
  key_prefix: "plexichat:"
```

A key `user:1` becomes `plexichat:user:1`.

### Input sanitization

Keys are sanitized: control characters are removed and length is limited to 512 characters. Do not store sensitive data in keys; use IDs only.

## Error handling

```python
from src.core.database import (
    ValkeyError,
    ValkeyConnectionError,
    ValkeyOperationError,
)

try:
    client.connect()
except ValkeyConnectionError as e:
    logger.error(f"Valkey unavailable: {e}")
    # Fall back to database-only mode

try:
    client.set("key", "value")
except ValkeyOperationError as e:
    logger.error(f"Valkey operation failed: {e}")
```

## Graceful degradation

If Valkey is unavailable, cached decorators and helpers fall back to direct execution:

```python
from src.core.database import cached, valkey_available

@cached(ttl=300)
def get_user(user_id: int) -> dict:
    return db.fetch_one("SELECT * FROM users WHERE id = ?", (user_id,))

# If Valkey is down, the function runs without caching
user = get_user(1)

if valkey_available():
    # Valkey-specific operations
    pass
```

## Testing

```bash
pytest src/tests/test_valkey.py -v
```

Tests use an in-memory mock. Integration tests are skipped when Valkey is unavailable.

---

# RAM cache module

RAM cache provides in-memory caching for small, rarely changing reference tables.

## When to use RAM cache vs Valkey

| Use case | RAM cache | Valkey |
| --- | --- | --- |
| Static reference data (categories, config) | Yes |  |
| User-specific data (settings, presence) |  | Yes |
| Session data |  | Yes |
| Data shared across instances |  | Yes |
| Frequently changing data |  | Yes |
| Tiny tables (< 100 rows) read 1000x/sec | Yes |  |

## Usage

```python
from src.core.database import RAMCache, create_cache, get_cache

# Create a cache for categories (1 hour TTL)
cache = create_cache("categories", ttl=3600)

# Load data from the database
cache.load(db, "SELECT * FROM search_categories ORDER BY position")

# Get all items
all_categories = cache.get_all()

# Get by field
gaming = cache.get("id", "gaming")

# Get multiple by field
selected = cache.get_many("id", ["gaming", "music", "tech"])

# Filter with a predicate
popular = cache.filter(lambda x: x["server_count"] > 100)

# Invalidate on write
cache.invalidate()
```

## Thread safety

The RAM cache is thread-safe through `threading.RLock`.

## Auto-reload

If the cache expires and has a stored query, it reloads from the database on the next access.

---

# Caching strategy

Plexichat uses a multi-tier strategy:

## Tier 1: RAM cache (in-process)

*   **TTL**: 1 hour
*   **Data**: Static reference tables such as `search_categories`.
*   **Latency**: ~1 microsecond

## Tier 2: Valkey cache (external)

*   **TTL**: 30 seconds to 10 minutes depending on data type.
*   **Data**: User data, server info, notification settings, sessions, presence.
*   **Latency**: ~0.5–2 ms

## Tier 3: Database (PostgreSQL or SQLite)

*   **Data**: All persistent data.
*   **Latency**: ~5–50 ms

## Cache keys

| Data type | Key pattern | TTL |
| --- | --- | --- |
| User profile | `user:{user_id}` | 60s |
| Server info | `server:{server_id}` | 300s |
| Notification settings | `notif_settings:{user_id}:{server_id}` | 600s |
| Session | `session:{session_id}` | 1800s |
| Presence | `presence:{user_id}` | 300s |
| Token verification | `token:{hash}` | 30s |

## Cache invalidation

Caches are invalidated on writes:

*   User update invalidates `user:*{user_id}*`.
*   Server update invalidates `server:{server_id}`.
*   Settings update invalidates `notif_settings:{user_id}:*`.

## Graceful degradation

If Valkey is unavailable, all operations fall back to direct database queries. The app continues to function with higher latency.

## Gateway opcodes and intents

For real-time cache invalidation events sent over the gateway — see `plexichat/src/api/websocket/opcodes.py:40` and `plexichat/src/core/events/types.py:120`:

*   Opcodes: `50 RATCHET_UPDATE`, `60 ARTIFACT_SUBSCRIBE`, `61 ARTIFACT_UNSUBSCRIBE`, `62 ARTIFACT_OP`, `63 ARTIFACT_SYNC`, `64 SUM_SUBSCRIBE`, `65 SUM_UNSUBSCRIBE`, `66 SUM_OP`, `67 SUM_SYNC`.
*   Intents: `1<<20 ARTIFACTS` and `1<<21 SUMS`.
