# Healthchecks

Understand and monitor health for each Plexichat Docker service.

## What are healthchecks

Healthchecks continuously verify that a service is running and responsive. Docker uses them to enforce startup order and to mark unhealthy containers.

Benefits:

*   Early failure detection
*   Correct startup order through `depends_on` conditions
*   Clear status in `docker compose ps`
*   Automatic restart with `restart: unless-stopped`

## View service health

List services and their health:

```bash
docker compose ps
```

Expected output:

```text
NAME       STATUS              HEALTH
backend    Up 5 minutes        healthy
db         Up 5 minutes        healthy
valkey     Up 5 minutes        healthy
rustfs     Up 5 minutes        healthy
client     Up 5 minutes        healthy
clamav     Up 5 minutes        healthy
backup     Up 5 minutes        healthy
```

Status colors:

*   **Healthy (green):** The health check passed.
*   **Starting (yellow):** The start period has not elapsed or checks are still running.
*   **Unhealthy (red):** Consecutive checks failed.

## Healthcheck details

### Backend API healthcheck

The backend defines the same probe in `plexichat/Dockerfile` and `docker-compose.yml`.

*   **Liveness probe:** `GET /api/v1/ready` (`src/api/routes/health.py:18`). Returns `200` and `{"status": "ready"}` as soon as the HTTP server is bound. No database, Valkey, or filesystem check. Docker uses this probe.
*   **Deep health:** `GET /api/v1/health` and `GET /health` (`src/api/routes/health.py:46` and `src/api/app.py:575`). Returns `status`, `version`, `events_metrics`, `is_queue_recreate_pending`, and `cross_worker_listener`. Each metric is advisory and wrapped in `try/except`.

**Docker healthcheck:**

```yaml
test: ["CMD-SHELL", "curl -sf http://localhost:8000/api/v1/ready"]
interval: 15s
timeout: 5s
retries: 3
start_period: 10s
```

If 3 consecutive checks fail after the 10-second start period, Docker marks the backend as unhealthy.

**Manual tests:**

```bash
curl -f http://localhost:8000/api/v1/ready
# Expected: {"status": "ready"}

curl -f http://localhost:8000/api/v1/health | python3 -m json.tool
curl -f http://localhost:8000/health | python3 -m json.tool
# Expected excerpt:
# {
#   "status": "healthy",
#   "version": "a.1.0-173",
#   "events_metrics": {},
#   "is_queue_recreate_pending": false,
#   "cross_worker_listener": {}
# }

docker compose exec backend curl -sf http://localhost:8000/api/v1/ready
```

### PostgreSQL healthcheck

*   **Image:** `postgres:18-alpine`
*   **Command:** `pg_isready -U ${POSTGRES_USER} -d ${POSTGRES_DB}`

**Docker healthcheck:**

```yaml
test: ["CMD-SHELL", "pg_isready -U $${POSTGRES_USER} -d $${POSTGRES_DB}"]
interval: 10s
timeout: 5s
retries: 10
```

**Manual test:**

```bash
docker compose exec db pg_isready -U plexichat -d plexichat
# Expected: accepting connections

docker compose exec db psql -U plexichat -d plexichat -c "SELECT 1;"
```

### Valkey healthcheck

*   **Image:** `valkey/valkey:9.1.1-alpine`
*   **Command:** Authenticated ping with `REDISCLI_AUTH` to avoid leaking the password in the process list.

**Docker healthcheck:**

```yaml
test: ["CMD-SHELL", "REDISCLI_AUTH=\"$${VALKEY_PASSWORD:?VALKEY_PASSWORD must be set}\" redis-cli ping | grep PONG"]
interval: 10s
timeout: 5s
retries: 10
```

**Manual tests:**

```bash
docker compose exec valkey sh -c 'REDISCLI_AUTH="$VALKEY_PASSWORD" redis-cli ping'
# Expected: PONG

docker compose exec valkey sh -c 'REDISCLI_AUTH="$VALKEY_PASSWORD" redis-cli INFO stats | grep connected_clients'
```

### RustFS healthcheck

*   **Image:** `rustfs/rustfs:1.0.0-rc.3`
*   **Endpoints:** `GET http://127.0.0.1:9000/health` (S3) and `GET http://127.0.0.1:9001/rustfs/console/health` (console)

**Docker healthcheck:**

```yaml
test: ["CMD-SHELL", "curl -f http://127.0.0.1:9000/health && curl -f http://127.0.0.1:9001/rustfs/console/health"]
interval: 15s
timeout: 10s
retries: 10
start_period: 40s
```

**Manual tests:**

```bash
docker compose exec rustfs sh -c 'curl -f http://127.0.0.1:9000/health && curl -f http://127.0.0.1:9001/rustfs/console/health'
# Expected (both): OK

# From the host (only if you publish the ports for debugging):
curl -f http://localhost:9000/health
curl -f http://localhost:9001/rustfs/console/health
```

### ClamAV healthcheck

*   **Image:** `clamav/clamav:1.5.4`
*   **Port:** `3310`

**Docker healthcheck:**

```yaml
test: ["CMD-SHELL", "echo PING | nc -w 2 localhost 3310 | grep PONG"]
interval: 30s
timeout: 10s
retries: 5
start_period: 120s
```

**Manual test:**

```bash
docker compose exec clamav sh -c 'echo PING | nc -w 2 localhost 3310 | grep PONG'
# Expected: PONG

docker compose logs clamav --tail 50
```

### Backup healthcheck

*   **Image:** `alpine:3.24` with `restic` and `supercronic 0.2.33`

**Docker healthcheck:**

```yaml
test: ["CMD-SHELL", "/scripts/healthcheck.sh"]
interval: 5m
timeout: 10s
retries: 3
start_period: 2m
```

The script checks that supercronic is running and that a recent successful backup marker exists. It catches dead cron loops, broken mounts, empty `BACKUP_RESTIC_PASSWORD`, and failed runs.

**Manual test:**

```bash
docker exec backup /scripts/healthcheck.sh; echo $?
docker exec backup /scripts/backup.sh list
```

### Client (Nginx) healthcheck

*   **Image:** `nginx:stable-alpine` (built from `plexichat-client/Dockerfile`)
*   **Docker healthcheck:** None is defined in `docker-compose.yml`.

**Manual test:**

```bash
curl -f http://localhost/
curl -fk https://localhost/
docker compose logs client --tail 50
```

### Optional services healthchecks

*   **Shieldstral (profile `ai`):** `curl -sf http://localhost:8080/health`, interval 15s, timeout 10s, 10 retries, start period 60s. Image `ghcr.io/ggml-org/llama.cpp:server-b10614`.
*   **Meilisearch (profiles `search`, `search-meilisearch`):** `wget -qO- http://localhost:7700/health | grep -q ok || curl -sf http://localhost:7700/health`, interval 10s, timeout 5s, 10 retries, start period 15s. Image `getmeili/meilisearch:v1.12`.
*   **Elasticsearch (profile `search-elastic`):** `curl -sf http://localhost:9200/_cluster/health | grep -q green || curl -sf http://localhost:9200/ | grep -q plexichat || exit 1`, interval 15s, timeout 10s, 10 retries, start period 60s. Image `docker.elastic.co/elasticsearch/elasticsearch:8.15.0`.

## Interpreting failures

### Backend is unhealthy

**Possible causes:**

1.  The backend process crashed.
2.  `curl` is missing or the port is not bound.
3.  A dependency did not become healthy and the backend never started.

**Debug steps:**

```bash
docker compose logs backend --tail 100
docker compose exec backend curl -v http://localhost:8000/api/v1/ready
docker compose ps
docker compose config | grep -A 5 "depends_on"
```

Check dependencies directly:

```bash
docker compose exec backend sh -c 'REDISCLI_AUTH="$VALKEY_PASSWORD" redis-cli -h valkey ping'
docker compose exec backend curl -f http://rustfs:9000/health
docker compose exec backend sh -c 'echo PING | nc -w 2 clamav 3310 | grep PONG'
```

**Fix:**

```bash
docker compose restart backend
# If the database is down:
docker compose restart db
# If the pool is saturated:
docker compose exec backend grep -A 5 "connection_pool" /app/config/docker-config.yaml
# Increase DB_POOL_MAX_CONNECTIONS in .env and restart
```

### Database is unhealthy

**Possible causes:**

1.  PostgreSQL failed to start.
2.  Disk is full.
3.  Data is corrupted.
4.  `POSTGRES_PASSWORD` is unset (compose fails fast) or port 5432 is in use on the host.

**Debug steps:**

```bash
docker compose logs db --tail 100
docker compose exec db pg_isready -U plexichat -d plexichat
docker compose exec db psql -U plexichat -d plexichat -c "SELECT 1;"
```

**Fix:**

```bash
docker compose restart db
# Restore from backup if needed:
docker compose down
cat backup.sql | docker compose exec -T db psql -U plexichat plexichat
docker compose up -d
```

### Valkey is unhealthy

**Possible causes:**

1.  Valkey process crashed.
2.  `VALKEY_PASSWORD` is unset (compose fails fast).
3.  Host port 6379 is in use or memory is exhausted.

**Debug steps:**

```bash
docker compose logs valkey --tail 100
docker compose exec valkey sh -c 'REDISCLI_AUTH="$VALKEY_PASSWORD" redis-cli ping'
docker compose exec valkey sh -c 'REDISCLI_AUTH="$VALKEY_PASSWORD" redis-cli INFO memory'
```

**Fix:**

```bash
docker compose restart valkey
# If out of memory, evict or flush (data is ephemeral):
docker compose exec valkey sh -c 'REDISCLI_AUTH="$VALKEY_PASSWORD" redis-cli FLUSHALL'
```

### RustFS is unhealthy

**Possible causes:**

1.  RustFS failed to start.
2.  Disk is full or `rustfs_data` is not writable by UID 10001.
3.  `S3_ACCESS_KEY` or `S3_SECRET_KEY` is unset.

**Debug steps:**

```bash
docker compose logs rustfs --tail 100
docker compose exec rustfs sh -c 'curl -f http://127.0.0.1:9000/health; echo ok'
docker compose exec rustfs df -h
docker compose run --rm rustfs-perms
```

**Fix:**

```bash
docker compose restart rustfs
# Fix ownership if needed:
docker compose run --rm rustfs-perms
docker compose up -d rustfs
```

## Dependency management

Services wait for dependencies before they start:

```text
rustfs-perms (chown) --> rustfs (healthy) --> rustfs-init (completed)
db (healthy) ─┐
valkey (healthy) ─┼--> backend (healthy) --> client (started, waits for cert-init completed)
rustfs-init (completed) ─┤
clamav (healthy) ─┘
db (healthy) --> backup
cert-init (completed) --> client
```

Compose conditions:

*   `service_healthy`: Wait until the health check passes.
*   `service_completed_successfully`: Wait until the one-shot container exits with status 0 (used for `rustfs-init`, `rustfs-perms`, and `cert-init`).
*   `service_started`: Do not wait for health (used for `client` -> `backend`).

View actual dependencies:

```bash
docker compose config | grep -A 3 "depends_on"
```

Example:

```yaml
backend:
  depends_on:
    db:
      condition: service_healthy
    valkey:
      condition: service_healthy
    rustfs-init:
      condition: service_completed_successfully
    clamav:
      condition: service_healthy
client:
  depends_on:
    backend:
      condition: service_started
    cert-init:
      condition: service_completed_successfully
```

## Tune healthcheck intervals

The defaults work for most deployments. If a service is slow to start, increase `start_period`:

```yaml
services:
  backend:
    healthcheck:
      start_period: 60s
      interval: 15s
      timeout: 5s
      retries: 3
```

For faster feedback on a quick service, decrease `interval`:

```yaml
services:
  valkey:
    healthcheck:
      interval: 5s
```

## Restart behavior

All long-running services use `restart: unless-stopped`. One-shot services (`rustfs-init`, `rustfs-perms`, `cert-init`) use `restart: "no"` so they exit after completion and do not block dependents.

Check restart history:

```bash
docker compose ps -a
docker events --filter 'type=container' | grep restart
```

## Manual health script

Run checks without relying on Docker status:

```bash
#!/bin/bash
set -e
echo "Backend liveness..."
curl -f http://localhost:8000/api/v1/ready || echo "FAILED"

echo "Backend deep health..."
curl -f http://localhost:8000/health || echo "FAILED"

echo "Database..."
docker compose exec db pg_isready -U plexichat -d plexichat || echo "FAILED"

echo "Valkey..."
docker compose exec valkey sh -c 'REDISCLI_AUTH="$VALKEY_PASSWORD" redis-cli ping | grep -q PONG' || echo "FAILED"

echo "RustFS..."
docker compose exec rustfs sh -c 'curl -f http://127.0.0.1:9000/health && curl -f http://127.0.0.1:9001/rustfs/console/health' || echo "FAILED"

echo "ClamAV..."
docker compose exec clamav sh -c 'echo PING | nc -w 2 localhost 3310 | grep -q PONG' || echo "FAILED"

echo "Backup..."
docker exec backup /scripts/healthcheck.sh || echo "FAILED"

echo "Frontend..."
curl -f http://localhost/ > /dev/null || echo "FAILED"
```

Save as `check-health.sh`:

```bash
chmod +x check-health.sh
./check-health.sh
```

## Troubleshoot healthcheck issues

### Healthcheck is stuck in "starting"

The `start_period` is too short or the check command hangs. Increase `start_period` and `timeout`.

### Too many restarts

The healthcheck is too strict or there is an underlying failure. Check logs and increase `retries` only after you fix the root cause:

```bash
docker compose logs backend | grep -i health
```

### False positives

The service works but the check fails. This is usually a network or timing issue. Test the command manually inside the container:

```bash
docker compose exec backend curl -v http://localhost:8000/api/v1/ready
docker compose exec backend sh -c 'REDISCLI_AUTH="$VALKEY_PASSWORD" redis-cli -h valkey ping'
```

Adjust the command or add a timeout.

## Next steps

*   [Architecture](architecture.md) for service topology
*   [Configuration](configuration.md) for environment variables
*   [Troubleshooting](troubleshooting.md) for common issues
