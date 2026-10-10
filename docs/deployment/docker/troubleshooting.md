# Troubleshooting

Fix common Docker issues.

## Services do not start

### Error: `port already in use`

**Message:**

```
Error response from daemon: Ports are not available: exposing port TCP 0.0.0.0:8000 -> 0.0.0.0:8000: listen tcp 0.0.0.0:8000: bind: address already in use
```

**Cause:** Another process already uses 8000, 80, 443, or 30000–30100/UDP (the backend voice range at `plexichat/docker-compose.yml:205`).

**Fix:**

Find the process that uses the port:

```bash
lsof -i :8000
lsof -i :443
lsof -i UDP:30000
```

Choose one option:

**Option 1 - Stop the conflicting process:**

```bash
kill -9 <PID>
docker compose up -d
```

**Option 2 - Change the host port in `plexichat/docker-compose.yml:205`:**

```yaml
backend:
  ports:
    - "8001:8000"          # Use 8001 on the host
    - "30001-30101:30000-30100/udp"

client:
  ports:
    - "8080:80"            # Use 8080 for HTTP
    - "8443:443"           # Use 8443 for HTTPS
```

Then restart:

```bash
docker compose down
docker compose up -d
```

**Note:** When you scale the backend (`docker compose up --scale backend=4`), remove the `ports` section on `backend` entirely. Multiple containers cannot bind the same host port.

### Error: `no such volume`

**Message:**

```
Error response from daemon: volume "plexichat_postgres_data" not found
```

**Cause:** Volumes were deleted or corrupted.

**Fix:**

Recreate the stack and volumes:

```bash
docker compose down -v
docker compose up -d
```

Or create volumes manually:

```bash
docker volume create plexichat_postgres_data
docker volume create plexichat_valkey_data
docker volume create plexichat_rustfs_data
```

## Backend does not start

### Backend stuck in `starting`

**Cause:** Migrations are still running or the database is not ready.

**Fix:**

Check logs:

```bash
docker compose logs -f backend --tail 100
```

Wait for:

```
INFO: Application startup complete
```

If the backend stays in `starting` for more than 60 seconds:

1. Database not ready - Wait longer or restart it:

   ```bash
   docker compose restart db
   ```

2. Corrupted database - Restore from a restic snapshot (`docker compose exec backup /scripts/backup.sh list`).
3. Insufficient memory - Increase Docker memory to at least 4 GB.

### Error: `Cannot connect to database`

**Message:**

```
ERROR: Failed to connect to database at db:5432
```

**Cause:** PostgreSQL is not running or not yet healthy.

**Fix:**

Check database status:

```bash
docker compose ps db
```

Expected:

```
STATUS: Up 2 minutes (healthy)
```

If not healthy, restart it:

```bash
docker compose restart db
```

Then check database logs:

```bash
docker compose logs db --tail 50
```

The health check runs `pg_isready -U ${POSTGRES_USER}` (`plexichat/docker-compose.yml:36`).

### Error: `Could not connect to Valkey`

**Message:**

```
ERROR: Failed to connect to valkey at valkey:6379
```

**Cause:** Valkey is not running or the password is wrong. Valkey fails fast if `VALKEY_PASSWORD` is empty (`plexichat/docker-compose.yml:49`).

**Fix:**

Verify Valkey is healthy:

```bash
docker compose ps valkey
docker compose exec valkey sh -c 'REDISCLI_AUTH="$VALKEY_PASSWORD" redis-cli ping'
```

Expected: `PONG` (`plexichat/docker-compose.yml:55`).

If it fails, restart Valkey:

```bash
docker compose restart valkey
```

Verify the password in `.env` matches the Compose interpolation:

```bash
grep VALKEY_PASSWORD plexichat/.env
```

### Error: `RustFS bucket creation failed`

**Message:**

```
ERROR: Failed to create bucket plexichat-media in RustFS
```

**Cause:** RustFS is not yet healthy or the bucket already exists. RustFS is internal-only (`expose: 9000/9001` at `plexichat/docker-compose.yml:91`) with health check `curl -f http://127.0.0.1:9000/health` (`plexichat/docker-compose.yml:95`).

**Fix:**

Check that RustFS is healthy before `rustfs-init` runs:

```bash
docker compose ps rustfs
docker compose logs rustfs --tail 50
```

`rustfs-init` retries until `rc alias set` succeeds (`plexichat/docker-compose.yml:108`). If you need to run it manually:

```bash
docker compose exec rustfs sh -c 'curl -sf http://127.0.0.1:9000/health && echo ok'
docker compose run --rm --entrypoint /bin/sh rustfs-init -c \
  'rc alias set local http://rustfs:9000 "$S3_ACCESS_KEY" "$S3_SECRET_KEY" && rc mb local/$S3_BUCKET || true'
```

Do not expose RustFS ports to the host unless you need direct debugging. If you do, add a temporary `ports` override as described in [index.md](index.md#access-points).

## Database issues

### PostgreSQL does not start

**Log:**

```
FATAL: could not create shared memory segment: No space left on device
```

**Cause:** Disk or `/dev/shm` is full.

**Fix:**

Check the volume and disk usage:

```bash
docker volume inspect plexichat_postgres_data
df -h
docker system df
```

Clean up and restart:

```bash
docker system prune -a
docker compose down -v
# Free disk space on the host if needed
docker compose up -d
```

### Connection refused inside the backend

**Message:**

```
pg8000.exceptions.InterfaceError: Can't create a connection to host db and port 5432 (timeout is 10 and source_address is None).
```

**Cause:** PostgreSQL crashed or the connection settings are wrong.

**Fix:**

Restart the database:

```bash
docker compose restart db
```

Verify connection settings inside the backend:

```bash
docker compose exec backend env | grep POSTGRES
```

Expected:

```
POSTGRES_HOST=db
POSTGRES_PORT=5432
POSTGRES_USER=plexichat
```

### Database locked or slow queries

**Log:**

```
WARNING: Query took 15.3s - possible database lock
```

**Cause:** Long-running transaction or missing index.

**Fix:**

List active queries:

```bash
docker compose exec db psql -U plexichat -d plexichat -c \
  "SELECT pid, query, query_start FROM pg_stat_activity WHERE state='active';"
```

Terminate a stuck query if safe:

```bash
docker compose exec db psql -U plexichat -d plexichat -c \
  "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE pid != pg_backend_pid() AND state='active' AND query_start < now() - interval '5 minutes';"
```

Check backend logs for slow query warnings:

```bash
docker compose logs backend | grep -i "slow"
```

## Connection pool exhaustion

### Error: `No available connections`

**Message:**

```
ERROR: Failed to acquire database connection after 10 seconds
```

**Cause:** All connections in the pool are in use.

**Fix:**

Check pool settings:

```bash
docker compose exec backend env | grep DB_POOL
```

Increase the maximum connections in `plexichat/.env` (see `plexichat/.env.example:28`):

```bash
DB_POOL_MAX_CONNECTIONS=100
```

Then restart:

```bash
docker compose restart backend
```

See [Connection pooling](connection-pooling.md).

### Error: `Connection timeout`

**Message:**

```
WARN: Waiting for database connection... (5s)
```

**Cause:** Network latency or database overload.

**Fix:**

Increase the timeout:

```bash
# In plexichat/.env
DB_POOL_CONNECT_TIMEOUT=15
```

Restart and check load:

```bash
docker compose restart backend
docker stats --no-stream
```

If `db` CPU or memory is high, restart it:

```bash
docker compose restart db
```

## API and WebSocket issues

### HTTP 502 Bad Gateway

**Message:**

```
502 Bad Gateway
```

**Cause:** Backend is not healthy or Nginx cannot reach `backend:8000` (`plexichat-client/docker/nginx/default.conf:53`).

**Fix:**

Check backend health:

```bash
docker compose ps backend
curl -sf http://localhost:8000/api/v1/ready
curl -sf http://localhost:8000/api/v1/health | jq .
```

If not `healthy`, restart:

```bash
docker compose restart backend
docker compose logs backend --tail 50
```

### WebSocket connection fails

**Message:**

```
WebSocket: connection refused
```

**Cause:** Backend gateway is not ready or Nginx WebSocket proxy is misconfigured (`plexichat-client/docker/nginx/default.conf:90`).

**Fix:**

Test directly against the backend:

```bash
curl -i -N -H "Connection: Upgrade" -H "Upgrade: websocket" \
  http://localhost:8000/gateway
```

Expected: `101 Switching Protocols`.

Test through Nginx:

```bash
curl -i -N -H "Connection: Upgrade" -H "Upgrade: websocket" \
  https://localhost/gateway -k
```

If the direct test succeeds but the Nginx test fails, verify `client` is `healthy` and check Nginx logs:

```bash
docker compose logs client --tail 50
```

Otherwise restart the backend:

```bash
docker compose restart backend
```

### CORS errors

**Browser console:**

```
Access to XMLHttpRequest from origin 'http://localhost' has been blocked by CORS policy
```

**Cause:** Frontend origin is not in the API `cors_origins` allow list.

**Fix:**

Check the runtime CORS config:

```bash
docker compose exec backend grep -A 10 "cors" config/docker-config.yaml
```

Or inspect the API config endpoint:

```bash
curl -sf http://localhost:8000/api/v1/health | jq .
```

Ensure the frontend origin is allowed. For example in `plexichat/config/docker-config.yaml:1`:

```yaml
api:
  cors_origins:
    - http://localhost
    - https://localhost
    - https://yourdomain.com
```

## Memory and performance

### Container was OOM killed

**Message:**

```
Killed signal: SIGKILL (exit 137)
```

**Cause:** Container exceeded its memory limit. ClamAV baseline is ~1.2 GB and peaks at ~2.4 GB during signature reloads.

**Fix:**

Check usage:

```bash
docker stats --no-stream
```

Increase Docker memory in Docker Desktop (Settings > Resources > Memory) to at least 6–8 GB when ClamAV is enabled, or set limits:

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

**Cause:** Inefficient queries, tight loops, or high load.

**Fix:**

Monitor:

```bash
docker stats --no-stream
docker compose logs backend | grep -i ERROR
```

If CPU stays high, restart the backend:

```bash
docker compose restart backend
```

## Disk space issues

### Error: `No space left on device`

**Cause:** Docker volumes or images fill the disk.

**Fix:**

Check usage:

```bash
df -h
docker system df
```

Clean up:

```bash
docker system prune -a
docker image prune -a
```

If you must wipe all Compose data:

```bash
docker compose down -v
docker compose up -d
```

## Build issues

### Docker build fails

**Message:**

```
ERROR: failed to solve with frontend dockerfile.v0
```

**Cause:** Dependency, network, or Dockerfile syntax error. The backend image uses `python:3.14.8-slim-trixie` (`plexichat/Dockerfile:1`).

**Fix:**

Rebuild without cache:

```bash
docker compose build --no-cache backend
```

Validate the Dockerfile:

```bash
docker buildx build --file plexichat/Dockerfile --tag test:latest plexichat
```

## Configuration issues

### Error: `Invalid environment variable`

**Message:**

```
ERROR: configuration error: invalid DB_POOL_MAX_CONNECTIONS value
```

**Cause:** Invalid value type in `plexichat/.env`.

**Fix:**

Check the value:

```bash
grep DB_POOL plexichat/.env
```

It must be an integer:

```
DB_POOL_MAX_CONNECTIONS=100   # Correct
DB_POOL_MAX_CONNECTIONS=100x  # Wrong
```

Fix it and restart:

```bash
docker compose restart backend
```

### Services cannot resolve each other

**Message:**

```
ERROR: name resolution failed: getaddrinfo EAI_NONAME
```

**Cause:** Services are not on the same Docker network.

**Fix:**

Inspect networks:

```bash
docker network ls
docker compose config | grep -A 20 "networks:"
```

All core services use the default `plexichat_default` bridge network. Do not add custom `networks` blocks unless you attach every service to the same network.

## Recovery procedures

### Full reset

Remove everything and start fresh (deletes all data):

```bash
docker compose down -v
docker system prune -a
docker compose up -d
```

### Restore from backup

```bash
# List available snapshots
docker compose exec backup /scripts/backup.sh list

# Stop writers if you restore the database
docker compose stop backend

# Restore the database from the latest snapshot
docker compose exec backup /scripts/backup.sh restore-db latest

# Or restore files to a temporary location
docker compose exec backup /scripts/backup.sh restore latest /tmp/restore

# Restart
docker compose up -d
```

### Roll back to a previous version

```bash
git checkout HEAD~1
docker compose build --no-cache
docker compose down
docker compose up -d
```

## Get help

### Collect debug information

Create a diagnostic bundle (remove secrets before sharing):

```bash
docker compose logs --tail 500 > logs.txt
docker compose ps -a >> logs.txt
docker stats --no-stream >> logs.txt
docker system df >> logs.txt
```

### Enable debug logging

```bash
LOG_LEVEL=DEBUG docker compose up -d
docker compose logs -f backend
```

### Check Docker daemon logs

**macOS:**

```bash
tail -f ~/Library/Containers/com.docker.docker/Data/log/vm/dockerd.log
```

**Linux:**

```bash
journalctl -xu docker.service
```

**Windows:**

```
%LOCALAPPDATA%\Docker\log\vm\dockerd.log
```

## Next steps

- [Configuration](configuration.md) - Configuration reference.
- [Production setup](production-setup.md) - Production hardening.
- [Health checks](healthchecks.md) - Service health details.
