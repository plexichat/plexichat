# Connection Pooling

Understanding and tuning database and Redis connection pools for Plexichat.

## What is Connection Pooling?

Without pooling: Each API request creates a new database connection, authenticates, and closes it after the request.

With pooling: A fixed number of connections are created and reused across requests, reducing overhead.

**Benefits:**
- Faster responses (reuse existing connections)
- Fewer database errors (prevents connection exhaustion)
- Lower database CPU (fewer handshakes)
- Better resource utilization

## PostgreSQL Connection Pool

### Default Settings

Development:
```
min_connections: 5
max_connections: 20
```

Production (high-concurrency):
```
min_connections: 20
max_connections: 100-200
```

### How Pooling Works

1. Backend starts with `min_connections` idle connections ready
2. As requests come in, connections are allocated
3. Once `max_connections` is reached, new requests wait
4. When requests finish, connections return to the pool
5. Idle connections beyond `min_connections` are closed

### Configuration

Set in `.env` or `docker-compose.yml`:

```bash
DB_POOL_MIN_CONNECTIONS=5
DB_POOL_MAX_CONNECTIONS=20
DB_POOL_CONNECT_TIMEOUT=10
DB_POOL_MAX_IDLE_TIME=300
```

Verify:
```bash
docker compose exec backend grep "min_connections\|max_connections" config/docker-config.yaml
```

### Tuning Guidelines

**For concurrent WebSocket users:**

- 1-10 users: min=5, max=20
- 10-50 users: min=10, max=50
- 50-200 users: min=20, max=100
- 200+ users: min=50, max=200+

**For API-only workload:**

- Low traffic: min=3, max=10
- Medium traffic: min=5, max=30
- High traffic: min=20, max=100

**Rule of thumb:**
```
max_connections = (concurrent_users * 2) + overhead_connections
min_connections = max_connections / 4
```

Example: 50 concurrent users
```
max_connections = (50 * 2) + 10 = 110  -> use 100
min_connections = 100 / 4 = 25
```

### Monitoring Connection Pool

Check current pool usage:
```bash
docker compose exec backend grep -A 20 "connection_pool" config/docker-config.yaml
```

View active connections in database:
```bash
docker compose exec db psql -U plexichat -d plexichat -c \
  "SELECT count(*) FROM pg_stat_activity WHERE datname='plexichat';"
```

See connection spikes:
```bash
docker compose logs backend | grep "pool\|saturation"
```

### Connection Pool Saturation

When pool is fully utilized and requests are queuing:

```
[WARNING] DB pool saturation: 95% of connections in use
```

**Fix:**
1. Increase `DB_POOL_MAX_CONNECTIONS`
2. Reduce query time (add database indexes)
3. Scale backend to multiple instances

### Troubleshooting

**"too many connections" error:**
```
Check: max_connections in PostgreSQL
Increase DB_POOL_MAX_CONNECTIONS in .env
docker compose restart backend
```

**Connections hanging:**
```
Check query lock: SELECT * FROM pg_stat_activity WHERE state='active';
Kill stuck query: SELECT pg_terminate_backend(pid);
Increase DB_POOL_CONNECT_TIMEOUT if network is slow
```

**Memory leak (connection count keeps growing):**
```
Check for connection leaks in backend code
Restart backend: docker compose restart backend
Monitor: docker stats backend
```

## Redis Connection Pool

### Default Settings

Development:
```
max_connections: 50
```

Production:
```
max_connections: 100-200
```

### Configuration

Set in `.env`:

```bash
REDIS_CONNECTION_POOL=50
```

Or in environment:
```bash
export REDIS_CONNECTION_POOL=100
docker compose up
```

### Tuning Guidelines

Redis connections are typically lighter weight than database connections.

- Low traffic: 10-20
- Medium traffic: 50 (default)
- High traffic: 100-200

**Monitor Redis:**
```bash
docker compose exec redis redis-cli INFO stats | grep connected_clients
```

**If hitting max:**
```
[ERROR] Redis pool exhausted: Cannot allocate connection
Increase REDIS_CONNECTION_POOL in .env
docker compose restart backend
```

## Connection Timeout

### DB_POOL_CONNECT_TIMEOUT

Seconds to wait for a connection to become available:

```
DB_POOL_CONNECT_TIMEOUT=10  # Wait up to 10 seconds
```

**Symptoms of too-low timeout:**
```
[ERROR] Failed to acquire database connection within 5 seconds
```

**Fix:**
Increase timeout or max_connections:
```bash
DB_POOL_CONNECT_TIMEOUT=15
DB_POOL_MAX_CONNECTIONS=50
docker compose restart backend
```

### DB_POOL_MAX_IDLE_TIME

Seconds before idle connections are closed (reclaim resources):

```
DB_POOL_MAX_IDLE_TIME=300  # 5 minutes
```

Larger timeout = more memory used, but faster for spiky traffic
Smaller timeout = less memory, but slower warm-up

**Typical:** 300 seconds (5 minutes)

## Validation Interval

### DB_POOL_VALIDATION_INTERVAL

Seconds between connection health checks:

```
DB_POOL_VALIDATION_INTERVAL=60  # Check every 60 seconds
```

Ensures idle connections are still alive (network issues, DB restart).

If connection is dead, it's replaced on next request.

**Troubleshooting stale connections:**
If you see "connection reset" errors:
```bash
Reduce DB_POOL_VALIDATION_INTERVAL=30
docker compose restart backend
```

## Performance Impact

### Slow Queries Block Pool

If queries are slow, connections stay allocated longer:

```
[WARNING] Query took 8.5s - pool saturation risk
```

**Fix:**
1. Optimize slow queries (add indexes)
2. Increase pool size temporarily while optimizing
3. Monitor query performance

Check slow queries:
```bash
docker compose exec backend grep "slow query" config/docker-config.yaml
docker compose logs backend | grep "slow"
```

### Connection Churn

Opening/closing many connections wastes resources:

```
[DEBUG] Created 150 connections in 10 seconds (high churn)
```

**Fix:**
- Increase `min_connections` to keep more connections warm
- Reduce traffic spikes with rate limiting
- Add caching layer

## Monitoring Dashboard

Create a simple monitoring script:

```bash
#!/bin/bash
watch -n 5 'echo "=== DB Pool ==="; \
  docker compose exec backend grep "min\|max" config/docker-config.yaml; \
  echo "=== Active Connections ==="; \
  docker compose exec db psql -U plexichat -d plexichat -c \
    "SELECT count(*) FROM pg_stat_activity WHERE datname='"'"'plexichat'"'"';"; \
  echo "=== Redis Connections ==="; \
  docker compose exec redis redis-cli INFO stats | grep connected_clients'
```

Save as `monitor-pool.sh` and run:
```bash
chmod +x monitor-pool.sh
./monitor-pool.sh
```

## Best Practices

1. **Set appropriate defaults** for your user count
2. **Monitor pool saturation** in production
3. **Optimize slow queries** before increasing pool size
4. **Test pool settings** under realistic load
5. **Document your settings** for your team
6. **Review periodically** as usage grows

## Summary Table

| Setting | Development | Production | Purpose |
|---------|-------------|-----------|---------|
| min_connections | 5 | 20 | Initial pool size |
| max_connections | 20 | 100 | Max concurrent connections |
| connect_timeout | 10s | 10s | Wait time for available connection |
| max_idle_time | 300s | 300s | Timeout for idle connections |
| validation_interval | 60s | 60s | Health check frequency |

## Next Steps

- [Configuration](configuration.md) - All configuration options
- [Production Setup](production-setup.md) - Production deployment
- [Troubleshooting](troubleshooting.md) - Debug connection issues
