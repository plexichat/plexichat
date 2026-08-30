# Database engines

Pluggable database engine layer for PlexiChat. Each engine implements the `BaseEngine` interface so you can switch between SQLite and PostgreSQL without changing application code.

## Overview

The `engines` package abstracts engine-specific SQL and connection handling behind a common interface. The `Database` class selects an engine at startup based on configuration and delegates all pool and query operations to it.

All engines are synchronous and thread-safe. Connection pooling is built into each engine.

## Engines

### BaseEngine

Abstract base class defined in `base.py:5`. Implement this class to add a new database backend.

| Method | Description |
|--------|-------------|
| `connect(pool)` | Get a connection from the pool or create a standalone connection. |
| `create_pool(min_conn, max_conn)` | Create a thread-safe connection pool (implemented by concrete engines). |
| `close_connection(conn, pool, close_params)` | Return a connection to the pool or close it. |
| `get_pool_stats(pool)` | Return pool statistics (`active_connections`, `idle_connections`, `total_connections`, `utilization_percent`). |
| `get_table_exists_query(table_name)` | Return the SQL and parameters to check whether a table exists. |
| `get_insert_or_ignore_query(table, columns)` | Return an engine-specific `INSERT OR IGNORE` statement. |
| `get_upsert_query(table, columns, conflict_columns, update_columns)` | Return an engine-specific upsert statement. |

### SQLiteEngine

Implementation in `sqlite.py:261` using the Python `sqlite3` standard library.

* Enables WAL journal mode and `NORMAL` synchronous mode for concurrent reads.
* Enforces foreign keys with `PRAGMA foreign_keys=ON`.
* Configures a busy timeout (default 5000 ms) to handle contention.
* Creates the database file and parent directory automatically.
* Provides an in-memory database when `path` is set to `:memory:` (used for tests).

SQLite pooling is provided by `SqlitePool` (`sqlite.py:27`), a thread-safe pool that tracks checked-out connections:

* Reclaims idle connections that were checked out to a thread and never returned, including abandoned transactions.
* Never reclaims a connection while a cursor is in flight (`mark_in_flight`) or while a live thread holds an open transaction within the grace window.
* Supports configurable `min_size`, `max_size`, `idle_reclaim_seconds`, `connect_timeout`, `cache_size_kb`, `mmap_size`, and `journal_size_limit`.

Relevant PRAGMAs are configurable through `connection_pool` in your config file. See `sqlite.py:297` for defaults.

### PostgresEngine

Implementation in `postgres.py:14` using `psycopg2` and `ThreadedConnectionPool`.

* Uses `RealDictCursor` so rows are returned as dictionaries.
* Builds connection parameters from `config.postgres` without exposing passwords in logs.
* Enables TCP keepalives (`keepalives_idle`, `keepalives_interval`, `keepalives_count`, `tcp_user_timeout`).
* Supports `sslmode` configuration (`prefer` by default).
* Honors `connection_pool.connect_timeout` for pool creation and connection checkout.

If `psycopg2` is not installed, the engine raises `ImportError` with installation instructions. See `postgres.py:24`.

## Usage

Engines are normally used through the `Database` class, not directly:

```python
from src.core.database import Database

db = Database(config)
db.connect()  # Creates the pool using the configured engine
pool_stats = db.get_pool_stats()
```

To use an engine directly:

```python
from src.core.database.engines.sqlite import SqliteEngine
from src.core.database.engines.postgres import PostgresEngine

# SQLite
engine = SqliteEngine({"path": "data/database.db", "connection_pool": {}})
pool = engine.create_pool(min_conn=2, max_conn=10)
conn = engine.connect(pool)
# ... use conn ...
engine.close_connection(conn, pool)

# PostgreSQL
engine = PostgresEngine({"postgres": {"host": "localhost", "dbname": "plexichat"}})
pool = engine.create_pool(min_conn=2, max_conn=20)
conn = engine.connect(pool)
```

## Add a new engine

1. Create a new file in this directory (for example, `mysql.py`).
2. Subclass `BaseEngine` and implement all abstract methods.
3. Add engine selection logic in the `Database` class to instantiate your engine based on configuration.
4. Add tests that exercise `connect`, `close_connection`, `get_pool_stats`, and the SQL helper methods.

## Pool statistics

Both engines return a common statistics dictionary from `get_pool_stats`:

```python
{
    "active_connections": 3,
    "idle_connections": 7,
    "total_connections": 10,
    "max_connections": 20,
    "min_connections": 2,
    "utilization_percent": 15.0,
    "database_type": "sqlite",  # or "postgres"
}
```

For SQLite, `get_detailed_stats` (`sqlite.py:367`) also reports `page_size`, `page_count`, `database_size_bytes`, and `wal_size_bytes` when a connection is provided.
