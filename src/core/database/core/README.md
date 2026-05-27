# Database Core

Central database abstraction layer providing a unified interface across SQLite and PostgreSQL backends. Handles connection pooling, query execution, transaction management, caching, and background maintenance.

## Components

### `manager.py`
**DatabaseManager** - Central orchestrator for all database operations:
- Engine selection (SQLite vs PostgreSQL)
- Migration coordination and lifecycle
- Connection lifecycle management
- Configuration loading and validation

### `connection.py`
Connection management with pooling support:
- TCP and Unix socket connections
- SSL/TLS configuration
- Connection pool sizing (pool_size, max_overflow, timeout)
- Connection health checks and recovery

### `execution.py`
Query execution layer:
- `fetch_one`, `fetch_all`, `execute`, `execute_many`
- Result caching with TTL
- Prepared statement support
- Parameter binding and type coercion

### `transactions.py`
Transaction lifecycle management:
- `begin_transaction`, `commit`, `rollback`
- Nested transaction support via savepoints
- Automatic rollback on error
- Transaction isolation level configuration

### `compat.py`
Cross-database compatibility layer:
- SQL placeholder conversion (? -> %s for PostgreSQL)
- Database type detection and feature flags
- Dialect-specific SQL transformations
- Data type mapping between backends

### `maintenance.py`
Database health and maintenance:
- VACUUM and ANALYZE operations
- Index rebuilding and optimization
- Integrity checks and repair
- Scheduled maintenance tasks

### `metrics.py`
Performance monitoring:
- Query timing and latency tracking
- Connection pool utilization stats
- Cache hit/miss ratios
- Slow query detection and logging

### `types.py`
Custom database type mappings:
- SnowflakeID <-> INTEGER handling
- JSON serialization/deserialization
- Enum type mapping
- Timestamp precision configuration

### `worker.py`
Background worker pool:
- Async task queue for database maintenance
- Periodic VACUUM/ANALYZE scheduling
- Connection pool warmup
- Graceful shutdown handling

## Key Design Decisions

- **`?` placeholders** are used throughout the codebase and automatically converted to `%s` for PostgreSQL
- **Caching** uses both local memory (CappedDict) and Redis when available
- **Transactions** are used for all write operations involving multiple tables
- **Engine abstraction** allows switching between SQLite (development) and PostgreSQL (production)
