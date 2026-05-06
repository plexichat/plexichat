# Troubleshooting

Common Docker issues and solutions.

## Services Won't Start

### Error: "port already in use"

**Message:**
```
Error response from daemon: Ports are not available: exposing port TCP 0.0.0.0:8000 -> 0.0.0.0:8000: listen tcp 0.0.0.0:8000: bind: address already in use
```

**Cause:** Another process is using port 8000, 80, or 443

**Fix:**

Find process using port:
```bash
lsof -i :8000  # Which process uses 8000?
```

Option 1 - Stop other process:
```bash
kill -9 <PID>
```

Option 2 - Change ports in docker-compose.yml:
```yaml
backend:
  ports:
    - "8001:8000"  # Use 8001 instead

client:
  ports:
    - "8080:80"    # Use 8080 for HTTP
    - "8443:443"   # Use 8443 for HTTPS
```

Then restart:
```bash
docker compose down
docker compose up
```

### Error: "no such volume"

**Message:**
```
Error response from daemon: volume "plexichat_db-data" not found
```

**Cause:** Volumes were deleted or corrupted

**Fix:**

Create volumes:
```bash
docker volume create plexichat_db-data
docker volume create plexichat_redis-data
docker volume create plexichat_minio-data
```

Or remove and recreate:
```bash
docker compose down -v
docker compose up
```

## Backend Won't Start

### Backend stuck in "starting" state

**Cause:** Migrations taking too long or database not responding

**Fix:**

Check logs:
```bash
docker compose logs -f backend --tail 100
```

Wait for:
```
INFO: Application startup complete
```

If stuck, it's usually:
1. Database not ready - wait longer or restart db
2. Corrupted database - restore from backup
3. Insufficient memory - increase Docker memory limit

### Error: "Cannot connect to database"

**Message:**
```
ERROR: Failed to connect to database at db:5432
```

**Cause:** PostgreSQL not running or not ready

**Fix:**

Check database status:
```bash
docker compose ps db
```

Should be:
```
STATUS: Up X minutes (healthy)
```

If not healthy:
```bash
docker compose restart db
```

If still fails, check database logs:
```bash
docker compose logs db --tail 50
```

### Error: "Could not connect to Redis"

**Message:**
```
ERROR: Failed to connect to redis at redis:6379
```

**Cause:** Redis service not running or wrong password

**Fix:**

Verify Redis is running:
```bash
docker compose exec redis redis-cli ping
```

Should return `PONG`

If failed, restart:
```bash
docker compose restart redis
```

Check password:
```bash
grep REDIS_PASSWORD .env.generated
```

Ensure it matches in docker-compose.yml

### Error: "MinIO bucket creation failed"

**Message:**
```
ERROR: Failed to create bucket plexichat-media in MinIO
```

**Cause:** MinIO not ready or bucket already exists

**Fix:**

Check MinIO:
```bash
curl http://localhost:9000/minio/health/live
```

Manually create bucket:
```bash
docker compose exec minio /usr/bin/mc mb minio/plexichat-media --ignore-existing
```

## Database Issues

### PostgreSQL won't start

**Log:**
```
FATAL: could not create shared memory segment: No space left on device
```

**Cause:** Disk full or /dev/shm full

**Fix:**

Check disk space:
```bash
docker volume inspect plexichat_db-data | grep Mountpoint
# Then: df -h <mountpoint>
```

Clean up:
```bash
docker system prune -a
docker compose down -v
# Free up disk space if needed
docker compose up
```

### Connection refused

**Message:**
```
psycopg2.OperationalError: could not connect to server: Connection refused
```

**Cause:** PostgreSQL crashed or wrong connection string

**Fix:**

Restart database:
```bash
docker compose restart db
```

Check connection settings:
```bash
docker compose exec backend env | grep POSTGRES
```

Should show:
```
POSTGRES_HOST=db
POSTGRES_PORT=5432
POSTGRES_USER=plexichat
```

### Database locked / slow queries

**Log:**
```
WARNING: Query took 15.3s - possible database lock
```

**Cause:** Long-running transaction or missing indexes

**Fix:**

Check active queries:
```bash
docker compose exec db psql -U plexichat -d plexichat -c \
  "SELECT pid, query, query_start FROM pg_stat_activity WHERE state='active';"
```

Kill stuck query:
```bash
docker compose exec db psql -U plexichat -d plexichat -c \
  "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE pid != pg_backend_pid();"
```

Check for missing indexes:
```bash
docker compose logs backend | grep "slow"
```

## Connection Pool Exhaustion

### Error: "No available connections"

**Message:**
```
ERROR: Failed to acquire database connection after 10 seconds
```

**Cause:** All connections in pool are in use

**Fix:**

Check pool settings:
```bash
docker compose exec backend env | grep DB_POOL
```

Increase max connections:
```bash
# Edit .env or .env.generated
DB_POOL_MAX_CONNECTIONS=100

# Restart
docker compose restart backend
```

See [Connection Pooling](connection-pooling.md) for guidance.

### Error: "Connection timeout"

**Message:**
```
WARN: Waiting for database connection... (5s)
```

**Cause:** Network latency or database overloaded

**Fix:**

Increase timeout:
```bash
DB_POOL_CONNECT_TIMEOUT=15
docker compose restart backend
```

Check database load:
```bash
docker stats plexichat-db-1
```

If high CPU/memory, restart:
```bash
docker compose restart db
```

## API / WebSocket Issues

### HTTP 502 Bad Gateway

**Message:**
```
502 Bad Gateway
```

**Cause:** Backend container died or not responding to reverse proxy

**Fix:**

Check backend status:
```bash
docker compose ps backend
```

Should be `healthy`

If not, restart:
```bash
docker compose restart backend
```

Check logs:
```bash
docker compose logs backend --tail 50
```

### WebSocket connection failed

**Message:**
```
WebSocket: connection refused
```

**Cause:** Backend WebSocket gateway not ready or network issue

**Fix:**

Test WebSocket:
```bash
curl -i -N -H "Connection: Upgrade" -H "Upgrade: websocket" \
  http://localhost:8000/gateway
```

Should return HTTP 101 (Switching Protocols)

If failed, backend not responding:
```bash
docker compose restart backend
```

### CORS errors

**Browser Console:**
```
Access to XMLHttpRequest from origin 'http://localhost' has been blocked by CORS policy
```

**Cause:** Frontend and backend on different origins (not in same docker network)

**Fix:**

Check CORS config:
```bash
docker compose exec backend grep -A 10 "cors" config/docker-config.yaml
```

Ensure frontend origin is in allowed list:
```yaml
api:
  cors_origins:
    - http://localhost
    - https://localhost
```

## Memory & Performance

### Container OOM killed

**Message:**
```
Killed signal: SIGKILL (exit 137)
```

**Cause:** Container ran out of memory

**Fix:**

Check memory usage:
```bash
docker stats --no-stream
```

Increase Docker memory limit (in Docker Desktop settings):
Settings > Resources > Memory: 4GB -> 6GB or 8GB

Or set limits in compose:
```yaml
backend:
  deploy:
    resources:
      limits:
        memory: 4G
      reservations:
        memory: 2G
```

### High CPU usage

**Cause:** Inefficient queries, infinite loops, or high load

**Fix:**

Monitor CPU:
```bash
docker stats --no-stream
```

Check logs for errors:
```bash
docker compose logs backend | grep ERROR
```

If CPU stuck, restart:
```bash
docker compose restart backend
```

## Disk Space Issues

### Error: "No space left on device"

**Cause:** Docker volumes or images filled disk

**Fix:**

Check disk:
```bash
df -h
```

Clean up Docker:
```bash
docker system df  # See what's using space

docker system prune -a  # Remove unused images/volumes
```

Remove old images:
```bash
docker image prune -a
```

## Build Issues

### Docker build fails

**Message:**
```
ERROR: failed to solve with frontend dockerfile.v0
```

**Cause:** Dependency issue, network error, or invalid Dockerfile

**Fix:**

Rebuild without cache:
```bash
docker compose build --no-cache backend
```

Check Dockerfile syntax:
```bash
docker buildx build --file Dockerfile --tag test:latest .
```

## Configuration Issues

### Error: "Invalid environment variable"

**Message:**
```
ERROR: configuration error: invalid DB_POOL_MAX_CONNECTIONS value
```

**Cause:** Invalid value type in .env

**Fix:**

Check .env file:
```bash
cat .env.generated | grep DB_POOL
```

Should be integers:
```
DB_POOL_MAX_CONNECTIONS=100  # Correct
DB_POOL_MAX_CONNECTIONS=100x  # Wrong
```

Fix and restart:
```bash
docker compose restart backend
```

### Services can't find each other

**Message:**
```
ERROR: name resolution failed: getaddrinfo EAI_NONAME
```

**Cause:** Services not on same network

**Fix:**

Check networks:
```bash
docker network ls
```

Verify services are on same network:
```bash
docker compose config | grep -A 20 "networks:"
```

Services should be on `plexichat-backend` or `plexichat-frontend`

## Recovery Procedures

### Full reset

Remove everything and start fresh:
```bash
docker compose down -v
docker system prune -a
docker compose --profile dev up
```

### Restore from backup

```bash
# Stop services
docker compose stop

# Restore database
cat backup.sql | docker compose exec -T db psql -U plexichat plexichat

# Restart
docker compose up
```

### Rollback to previous version

```bash
git checkout HEAD~1  # Previous commit
docker compose build --no-cache
docker compose down
docker compose up
```

## Getting Help

### Collect debug info

Create diagnostic bundle:
```bash
docker compose logs --tail 500 > logs.txt
docker compose ps -a >> logs.txt
docker stats --no-stream >> logs.txt
docker system df >> logs.txt
```

Share `logs.txt` (without secrets!)

### Enable debug logging

```bash
LOG_LEVEL=DEBUG docker compose up
```

More verbose output for debugging.

### Check Docker daemon logs

macOS:
```bash
tail -f ~/Library/Containers/com.docker.docker/Data/log/vm/dockerd.log
```

Linux:
```bash
journalctl -xu docker.service
```

Windows:
```
%LOCALAPPDATA%\Docker\log\vm\dockerd.log
```

## Next Steps

- [Configuration](configuration.md) - Configuration help
- [Production Setup](production-setup.md) - Production issues
- [Healthchecks](healthchecks.md) - Service health
