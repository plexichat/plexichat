# Database Engines

Pluggable database engine implementations providing a common interface (`BaseEngine`) for SQLite and PostgreSQL backends. Allows transparent switching between databases without changing application code.

## Components

### `base.py`
**BaseEngine** - Abstract base class defining the engine interface:

- `connect()` / `disconnect()` - Connection lifecycle
- `execute()` / `fetch_one()` / `fetch_all()` - Query execution
- `execute_many()` - Batch operations
- `begin_transaction()` / `commit()` / `rollback()` - Transaction control
- `is_connected()` / `health_check()` - Connection status

### `sqlite.py`
**SQLiteEngine** - SQLite implementation using aiosqlite:

- WAL mode enabled for concurrent reads
- Foreign keys enforced
- Busy timeout for contention handling
- Automatic database file creation
- In-memory database support for testing

### `postgres.py`
**PostgresEngine** - PostgreSQL implementation using asyncpg:

- Connection URI parsing with all parameters
- SSL/TLS support with certificate validation
- Connection pooling with configurable pool size
- Prepared statement caching
- Schema namespace isolation

## Key Features

- **Async-first** - All engines use async/await for I/O
- **Pluggable** - Add new engines by implementing BaseEngine
- **Pool-aware** - Connection pooling built into each engine
- **Health checks** - Every engine exposes connection health status
- **Graceful degradation** - Falls back gracefully on connection failure
