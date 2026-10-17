# Connection pooling

Understand and tune database and Valkey connection pools for Plexichat.

## What is connection pooling

Without pooling, each API request creates a new database connection, authenticates, and closes it after the request.

With pooling, a fixed set of connections is created and reused across requests.

Benefits:

*   Faster responses because existing connections are reused
*   Fewer database errors from connection exhaustion
*   Lower database CPU usage because fewer handshakes are needed
*   Better resource utilization

## PostgreSQL connection pool

### Default settings

The `.env.example` defaults for Docker Compose are:

```text
min_connections: 5
max_connections: 20
```

For high-concurrency production, set:

```text
min_connections: 20
max_connections: 100 to 200
```

The built-in fallback in `src/config_defaults.py` is `min_connections: 5` and `max_connections: 100`. The effective value is the one you set in `.env`. See [Configuration](configuration.md).

### How pooling works

1.  The backend starts with `min_connections` idle connections.
2.  As requests arrive, connections are allocated from the pool.
3.  When `max_connections` is reached, new requests wait up to `DB_POOL_CONNECT_TIMEOUT` seconds.
4.  When a request finishes, the connection returns to the pool.
5.  Idle connections above `min_connections` are closed after `DB_POOL_MAX_IDLE_TIME` seconds.

The pool also reclaims checked-out connections that stay idle longer than `idle_reclaim_seconds` (5 seconds by default in `config_defaults.py`) instead of blocking for the full connect timeout. This recovers connections that leak into worker-thread storage.

### Configuration

Set these variables in `.env` or `docker-compose.yml`:

```bash
DB_POOL_MIN_CONNECTIONS=5
DB_POOL_MAX_CONNECTIONS=20
DB_POOL_CONNECT_TIMEOUT=10
DB_POOL_MAX_IDLE_TIME=300
DB_POOL_VALIDATION_INTERVAL=60
DB_POOL_ENABLE_VALIDATION=true
DB_POOL_VALIDATION_QUERY="SELECT 1"
```

Verify the values:

```bash
docker compose exec backend grep -A 5 "connection_pool" /app/config/docker-config.yaml
grep DB_POOL .env
```

### Tuning guidelines

#### For concurrent WebSocket users

*   1 to 10 users: `min=5`, `max=20`
*   10 to 50 users: `min=10`, `max=50`
*   50 to 200 users: `min=20`, `max=100`
*   More than 200 users: `min=50`, `max=200` or more

#### For API-only workloads

*   Low traffic: `min=3`, `max=10`
*   Medium traffic: `min=5`, `max=30`
*   High traffic: `min=20`, `max=100`

#### Rule of thumb

```text
max_connections = (concurrent_users * 2) + overhead
min_connections = max_connections / 4
```

Example for 50 concurrent users:

```text
max_connections = (50 * 2) + 10 = 110, use 100
min_connections = 100 / 4 = 25
```

### Monitoring the pool

Check pool usage from the config:

```bash
docker compose exec backend grep -A 20 "connection_pool" /app/config/docker-config.yaml
```

View active connections in PostgreSQL:

```bash
docker compose exec db psql -U plexichat -d plexichat -c \
  "SELECT count(*) FROM pg_stat_activity WHERE datname='plexichat';"
```

Check for saturation warnings:

```bash
docker compose logs backend | grep -i "pool\|saturation"
```

### Pool saturation

When the pool is fully used, requests queue:

```text
[WARNING] DB pool saturation: 95% of connections in use
```

Fix this by following these steps:

1.  Increase `DB_POOL_MAX_CONNECTIONS`.
2.  Reduce query time by adding indexes.
3.  Scale the backend with `docker compose up --scale backend=N` and remove the backend `ports` mappings so Nginx can load-balance.

### Troubleshooting

#### "Too many connections" error

```text
FATAL: too many connections for role "plexichat"
```

Check PostgreSQL `max_connections` and increase `DB_POOL_MAX_CONNECTIONS` or PostgreSQL `max_connections` as needed, then restart:

```bash
docker compose restart backend
```

#### Connections that hang

```sql
-- Find active queries
SELECT pid, state, query FROM pg_stat_activity WHERE state = 'active';

-- Terminate a stuck query
SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE pid = 12345;
```

If the network is slow, increase `DB_POOL_CONNECT_TIMEOUT`.

#### Connection count that keeps growing

This can indicate a leak. Restart the backend and monitor:

```bash
docker compose restart backend
docker stats plexichat-backend-1
```

If the count grows again, check backend code for connections that are not returned to the pool.

## Valkey connection pool

### Default settings

```text
Development: max_connections: 50
Production:  max_connections: 100 to 200
```

### Configuration

Set this variable in `.env`:

```bash
VALKEY_CONNECTION_POOL=50
```

Or export it before starting Compose:

```bash
export VALKEY_CONNECTION_POOL=100
docker compose up -d
```

### Tuning guidelines

Valkey connections are lighter than database connections:

*   Low traffic: 10 to 20
*   Medium traffic: 50 (default)
*   High traffic: 100 to 200

Monitor Valkey clients:

```bash
docker compose exec valkey redis-cli -a "$VALKEY_PASSWORD" INFO stats | grep connected_clients
# Or, using REDISCLI_AUTH so the password does not appear in ps output:
docker compose exec valkey sh -c 'REDISCLI_AUTH="$VALKEY_PASSWORD" redis-cli INFO stats | grep connected_clients'
```

If the pool is exhausted:

```text
[ERROR] Valkey pool exhausted: Cannot allocate connection
```

Increase `VALKEY_CONNECTION_POOL` and restart the backend:

```bash
docker compose restart backend
```

## Connection timeout

### DB_POOL_CONNECT_TIMEOUT

Seconds to wait for a connection when the pool is full:

```bash
DB_POOL_CONNECT_TIMEOUT=10
```

If this value is too low, you see:

```text
[ERROR] Failed to acquire database connection within 5 seconds
```

Increase `DB_POOL_CONNECT_TIMEOUT` or `DB_POOL_MAX_CONNECTIONS` and restart the backend.

### DB_POOL_MAX_IDLE_TIME

Seconds before an idle connection above `min_connections` is closed:

```bash
DB_POOL_MAX_IDLE_TIME=300
```

*   A larger value keeps more connections warm for spiky traffic but uses more memory.
*   A smaller value saves memory but requires more time to warm up.

The default is 300 seconds (5 minutes).

## Validation interval

### DB_POOL_VALIDATION_INTERVAL

Seconds between health checks for idle connections:

```bash
DB_POOL_VALIDATION_INTERVAL=60
```

This ensures idle connections are still alive after network issues or a database restart. If a connection is dead, it is replaced on the next request.

If you see `connection reset` errors, reduce the interval:

```bash
DB_POOL_VALIDATION_INTERVAL=30
docker compose restart backend
```

The query for validation is `DB_POOL_VALIDATION_QUERY` (default `SELECT 1`) and you can disable checks with `DB_POOL_ENABLE_VALIDATION=false`, which is not recommended for production.

## Performance impact

### Slow queries block the pool

Slow queries keep connections allocated longer:

```text
[WARNING] Query took 8.5s - pool saturation risk
```

Fix this by following these steps:

1.  Optimize slow queries by adding indexes.
2.  Increase pool size temporarily while you optimize.
3.  Monitor query performance with `slow_query_threshold_ms` (default 1000 ms in `src/config_defaults.py`).

Check slow queries:

```bash
docker compose logs backend | grep -i "slow"
docker compose exec backend grep "slow_query_threshold" /app/config/docker-config.yaml
```

### Connection churn

Opening and closing many connections wastes resources:

```text
[DEBUG] Created 150 connections in 10 seconds (high churn)
```

Fix this by following these steps:

*   Increase `DB_POOL_MIN_CONNECTIONS` to keep more connections warm.
*   Reduce traffic spikes with rate limiting.
*   Add caching with Valkey.

## Monitoring dashboard

Create a script to watch pool usage:

```bash
#!/bin/bash
watch -n 5 'echo "=== DB Pool ==="; \
  docker compose exec backend grep -A 5 "connection_pool" /app/config/docker-config.yaml; \
  echo "=== Active Connections ==="; \
  docker compose exec db psql -U plexichat -d plexichat -c \
    "SELECT count(*) FROM pg_stat_activity WHERE datname='"'"'plexichat'"'"';"; \
  echo "=== Valkey Clients ==="; \
  docker compose exec valkey sh -c '\''REDISCLI_AUTH="$VALKEY_PASSWORD" redis-cli INFO stats | grep connected_clients'\''; \
  echo "=== Valkey Ping ==="; \
  docker compose exec valkey sh -c '\''REDISCLI_AUTH="$VALKEY_PASSWORD" redis-cli ping'\'';'
```

Save the file as `monitor-pool.sh` and run it:

```bash
chmod +x monitor-pool.sh
./monitor-pool.sh
```

## Best practices

1.  Set defaults based on your expected concurrent user count.
2.  Monitor pool saturation in production.
3.  Optimize slow queries before increasing pool size.
4.  Test pool settings under realistic load.
5.  Document your settings for your team.
6.  Review settings periodically as usage grows.

## Summary table

| Setting | Development (.env.example) | Built-in fallback | Production recommendation | Purpose |
| --- | --- | --- | --- | --- |
| `min_connections` | `5` | `5` | `20` | Initial pool size |
| `max_connections` | `20` | `100` | `100` | Maximum concurrent connections |
| `connect_timeout` | `10s` | `10s` | `10s` | Wait time for a connection |
| `max_idle_time` | `300s` | `300s` | `300s` | Idle connection timeout |
| `validation_interval` | `60s` | `60s` | `60s` | Health check frequency |
| `idle_reclaim_seconds` | `5.0s` | `5.0s` | `5.0s` | Reclaim for leaked checked-out connections |

## Next steps

*   [Configuration](configuration.md) for all configuration options
*   [Architecture](architecture.md) for service topology
*   [Troubleshooting](troubleshooting.md) for connection issues
