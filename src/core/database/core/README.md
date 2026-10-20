# Database core

## Overview

Database core is the central abstraction layer over SQLite and PostgreSQL. It handles connection pooling, query execution, transactions, caching, and background maintenance. The `Database` class in `plexichat/src/core/database/core/manager.py:29` orchestrates engines, monitoring, and dialect helpers.

Related platform endpoints:

*   WebSocket gateway: `/gateway` — see `plexichat/src/api/websocket/gateway.py:261`. Use `/gateway`, not `/ws`.
*   Readiness probe: `GET /api/v1/ready` — see `plexichat/src/api/routes/health.py:18`.
*   Deep health check: `GET /api/v1/health` — see `plexichat/src/api/routes/health.py:46` with root alias at `GET /health` — see `plexichat/src/api/app.py:574`.

For gateway opcodes and intents, see `plexichat/src/api/websocket/opcodes.py:40` and `plexichat/src/core/events/types.py:120`.

## Components

### `manager.py` — see `plexichat/src/core/database/core/manager.py:29`

`Database` is the central orchestrator. It selects the engine (SQLite vs PostgreSQL), coordinates migrations, manages the connection lifecycle, and loads config — see `plexichat/src/config_defaults.py:64`.

### `connection.py`

Connection management with pooling:

*   TCP and Unix-socket connections.
*   SSL/TLS configuration through `database.postgres.sslmode`.
*   Pool sizing: `min_connections`, `max_connections`, `connect_timeout`.
*   Health checks and recovery.

### `execution.py`

Query execution layer:

*   `fetch_one`, `fetch_all`, `execute`, `execute_many`.
*   Result caching with TTL.
*   Prepared-statement support.
*   Parameter binding and type coercion.

All queries use `?` placeholders; PostgreSQL is converted to `%s` automatically — see `plexichat/src/core/database/core/compat.py`.

### `transactions.py`

Transaction lifecycle:

*   `begin_transaction`, `commit`, `rollback`.
*   Nested transactions through savepoints.
*   Automatic rollback on error.
*   Isolation-level configuration.

### `compat.py`

Cross-database compatibility:

*   Placeholder conversion (`?` to `%s` for PostgreSQL).
*   Database-type detection and feature flags.
*   Dialect-specific SQL transforms.
*   Data-type mapping between backends.

### `maintenance.py`

Health and maintenance:

*   `VACUUM` and `ANALYZE`.
*   Index rebuilding and optimization.
*   Integrity checks and repair.
*   Scheduled maintenance tasks.

### `metrics.py`

Performance monitoring:

*   Query timing and latency tracking.
*   Pool-utilization stats.
*   Cache hit and miss ratios.
*   Slow-query detection — threshold from `database.monitoring.slow_query_threshold_ms` — see `plexichat/src/config_defaults.py:101`.

### `types.py`

Custom type mappings:

*   `SnowflakeID` to `INTEGER`.
*   JSON serialization and deserialization.
*   Enum mapping.
*   Timestamp precision.

### `worker.py`

Background worker pool:

*   Async task queue for maintenance.
*   Periodic `VACUUM` and `ANALYZE` scheduling.
*   Pool warmup.
*   Graceful shutdown handling.

## Key design decisions

*   Use `?` placeholders everywhere. The compat layer converts them to `%s` for PostgreSQL — see `plexichat/src/core/database/core/compat.py`.
*   Caching uses local memory (`OrderedDict` with TTL) and Valkey when available — see `plexichat/src/core/database/cache/` and `plexichat/src/config_defaults.py:110`.
*   All multi-table writes use transactions.
*   Engine abstraction lets you switch between SQLite (development) and PostgreSQL (production) through `database.type` — see `plexichat/src/config_defaults.py:80`.

## Configuration

```yaml
database:
  type: sqlite
  path: data/app.db
  postgres:
    host: localhost
    port: 5432
    user: postgres
    password: ""
    dbname: plexichat
    sslmode: prefer
  connection_pool:
    min_connections: 5
    max_connections: 100
    connect_timeout: 10
    max_connection_age_hours: 2.0
```

Global server keys use these paths — see `plexichat/src/config_defaults.py`:

*   `server.host`, `server.port`, `server.reload` — see `plexichat/src/config_defaults.py:70`. Do not use `api.host` or `api.port`.
*   `api.api_prefix` — default `/api/v1` — see `plexichat/src/config_defaults.py:130`. Do not use `api.prefix`.
*   `tls` — see `plexichat/src/config_defaults.py:618`. Do not use `api.ssl`.
*   `voice.sfu_backend` — default `aiortc` — see `plexichat/src/config_defaults.py:396`. Do not use `mediasoup`.

## Gateway opcodes and intents

If you emit database-driven events to gateway clients, use these values — see `plexichat/src/api/websocket/opcodes.py:40` and `plexichat/src/core/events/types.py:120`:

*   Opcodes: `50 RATCHET_UPDATE`, `60 ARTIFACT_SUBSCRIBE`, `61 ARTIFACT_UNSUBSCRIBE`, `62 ARTIFACT_OP`, `63 ARTIFACT_SYNC`, `64 SUM_SUBSCRIBE`, `65 SUM_UNSUBSCRIBE`, `66 SUM_OP`, `67 SUM_SYNC`, `68 ARTIFACT_OP_REJECTED`.
*   Intents: `1<<20 ARTIFACTS` and `1<<21 SUMS`.
