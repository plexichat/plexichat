# Healthchecks

Understanding and monitoring service health in Plexichat Docker.

## What are Healthchecks?

Healthchecks continuously verify that services are running and responsive. Docker automatically manages services with failing healthchecks.

**Benefits:**
- Early failure detection
- Automatic restart on failure
- Clear status visibility
- Dependency management (service B waits for service A to be healthy)

## Service Health Status

View all services and their health:

```bash
docker compose ps
```

Expected output:
```
NAME       STATUS              HEALTH
backend    Up 5 minutes        healthy
db         Up 5 minutes        healthy
redis      Up 5 minutes        healthy
minio      Up 5 minutes        healthy
client     Up 5 minutes        healthy
```

Colors:
- **Green (healthy)** - Service is responding correctly
- **Yellow (starting)** - Service is starting, health check in progress
- **Red (unhealthy)** - Service failed healthcheck, may restart

## Healthcheck Details

### Backend API Healthcheck

**Endpoint:** `GET /health`

**Test Command:**
```bash
curl http://localhost:8000/health
```

**Expected Response (200 OK):**
```json
{
  "status": "healthy",
  "version": "a.1.0-53",
  "timestamp": "2024-01-15T10:30:45Z",
  "database": "connected",
  "redis": "connected",
  "storage": "connected"
}
```

**Healthcheck Settings:**
```
Interval: 10 seconds
Timeout: 5 seconds
Retries: 3
Start period: 30 seconds
```

Meaning: After 30 seconds, if 3 consecutive checks fail over 30 seconds (3 x 10s), service marks as unhealthy.

**Manual Test:**
```bash
docker compose exec backend curl -v http://localhost:8000/health
```

### PostgreSQL Healthcheck

**Command:** `pg_isready`

**Manual Test:**
```bash
docker compose exec db pg_isready -U plexichat
```

**Expected Output:**
```
accepting connections
```

**Healthcheck Settings:**
```
Interval: 10 seconds
Timeout: 5 seconds
Retries: 3
Start period: 10 seconds
```

### Redis Healthcheck

**Command:** `redis-cli ping`

**Manual Test:**
```bash
docker compose exec redis redis-cli ping
```

**Expected Output:**
```
PONG
```

**Healthcheck Settings:**
```
Interval: 10 seconds
Timeout: 5 seconds
Retries: 3
Start period: 10 seconds
```

### MinIO Healthcheck

**Endpoint:** Health endpoint

**Manual Test:**
```bash
curl http://localhost:9000/minio/health/live
```

**Expected Response (200 OK):**
```
MinIO OK
```

**Healthcheck Settings:**
```
Interval: 10 seconds
Timeout: 5 seconds
Retries: 3
Start period: 10 seconds
```

### Client (Nginx) Healthcheck

**Test:** HTTP on port 80

**Manual Test:**
```bash
curl http://localhost/
```

**Expected Response:** HTML homepage

**Healthcheck Settings:**
```
Interval: 10 seconds
Timeout: 5 seconds
Retries: 3
Start period: 15 seconds
```

## Interpreting Healthcheck Failures

### Backend unhealthy

**Log:**
```
WARNING backend is unhealthy
```

**Possible Causes:**
1. Database connection failed
2. Redis connection failed
3. MinIO connection failed
4. Application crashed

**Debug Steps:**
```bash
# View logs
docker compose logs backend --tail 50

# Check dependencies
docker compose exec backend curl http://db:5432    # Should fail (not HTTP)
docker compose exec backend redis-cli -h redis ping # Should return PONG
docker compose exec backend curl http://minio:9000  # Should redirect
```

**Fix:**
```bash
# If DB is down
docker compose restart db
docker compose up

# If connection pool exhausted
docker compose exec backend grep "pool" config/docker-config.yaml
# Increase DB_POOL_MAX_CONNECTIONS
```

### Database unhealthy

**Log:**
```
WARNING db is unhealthy
```

**Possible Causes:**
1. PostgreSQL failed to start
2. Disk full
3. Corrupted data
4. Port conflict (another postgres running)

**Debug Steps:**
```bash
# View logs
docker compose logs db --tail 50

# Check if port 5432 is available
lsof -i :5432    # Should show only docker process

# Test connection
docker compose exec db psql -U plexichat -c "SELECT 1;"
```

**Fix:**
```bash
# Restart service
docker compose restart db

# If corrupted, restore from backup
docker compose down
cat backup.sql | docker compose exec -T db psql -U plexichat plexichat
docker compose up
```

### Redis unhealthy

**Log:**
```
WARNING redis is unhealthy
```

**Possible Causes:**
1. Redis process crashed
2. Port 6379 conflict
3. Out of memory

**Debug Steps:**
```bash
# View logs
docker compose logs redis --tail 50

# Test connection
docker compose exec redis redis-cli ping

# Check memory
docker compose exec redis redis-cli INFO memory
```

**Fix:**
```bash
# Restart
docker compose restart redis

# If out of memory, clear cache
docker compose exec redis redis-cli FLUSHALL

# Increase memory limit in docker-compose.yml
```

### MinIO unhealthy

**Log:**
```
WARNING minio is unhealthy
```

**Possible Causes:**
1. MinIO failed to start
2. Disk full
3. Port 9000 conflict

**Debug Steps:**
```bash
# View logs
docker compose logs minio --tail 50

# Test health endpoint
curl http://localhost:9000/minio/health/live

# Check disk usage
docker compose exec minio df -h
```

**Fix:**
```bash
# Restart
docker compose restart minio

# If disk full, remove old volumes and restart
docker volume prune
docker compose up
```

## Dependency Management

Services wait for dependencies to be healthy before starting:

```
bootstrap -> db, redis, minio
minio-init -> minio
backend -> bootstrap, db, redis, minio-init
client -> backend, cert-init
```

**Example timeline:**
1. `bootstrap` starts (creates config)
2. `db`, `redis`, `minio` start in parallel
3. `minio-init` waits for `minio` to be healthy, then initializes buckets
4. `backend` waits for all above to be healthy before starting
5. `client` waits for `backend` to be healthy before starting

This ensures startup order is correct and dependencies are ready.

## Checking Service Dependencies

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
    redis:
      condition: service_healthy
    minio-init:
      condition: service_completed_successfully
```

Conditions:
- `service_healthy` - Wait for healthcheck to pass
- `service_completed_successfully` - Wait for service to finish and exit (for one-time tasks like bootstrap)
- `service_started` - Don't wait (service just needs to be running)

## Healthcheck Interval Tuning

Default intervals (10 seconds) work for most cases.

**For slow-starting services**, increase start_period:

```yaml
backend:
  healthcheck:
    start_period: 60s  # Wait 60 seconds before first check
    interval: 10s
    timeout: 5s
    retries: 3
```

**For fast services**, decrease interval:

```yaml
redis:
  healthcheck:
    interval: 5s  # Check every 5 seconds instead of 10
```

## Automatic Restart

Services automatically restart if unhealthy:

```yaml
restart_policy:
  condition: on-failure
  max_attempts: 3
  delay: 10s
```

Means:
- Restart if healthcheck fails
- Try up to 3 times
- Wait 10 seconds between restarts

Check restart history:
```bash
docker compose ps -a
```

View restart events:
```bash
docker events --filter 'type=container' | grep restart
```

## Manual Health Checks

Run healthchecks without relying on Docker:

```bash
#!/bin/bash
echo "Backend health..."
curl -f http://localhost:8000/health || echo "FAILED"

echo "Database health..."
docker compose exec db pg_isready -U plexichat || echo "FAILED"

echo "Redis health..."
docker compose exec redis redis-cli ping || echo "FAILED"

echo "MinIO health..."
curl -f http://localhost:9000/minio/health/live || echo "FAILED"

echo "Frontend health..."
curl -f http://localhost/ > /dev/null || echo "FAILED"
```

Save as `check-health.sh` and run:
```bash
chmod +x check-health.sh
./check-health.sh
```

## Monitoring Healthchecks

Long-term monitoring:

```bash
# Watch health status continuously
watch -n 5 'docker compose ps'

# Export to file for graphing
watch -n 60 'date >> health.log; docker compose ps >> health.log'
```

Integration with monitoring tools:

```bash
# Send to Prometheus
curl -s http://localhost:8000/metrics | grep health

# Send to datadog
docker-compose logs backend | grep "health"
```

## Troubleshooting Healthcheck Issues

### Healthcheck hangs (stuck in "starting" state)

**Cause:** start_period too short or check command hangs

**Fix:**
```yaml
healthcheck:
  start_period: 60s    # Increase from 30s
  timeout: 10s         # Increase from 5s
```

### Too many restarts

**Cause:** Healthcheck too strict or underlying issue

**Check logs:**
```bash
docker compose logs backend | grep restart
```

**Increase retry threshold:**
```yaml
healthcheck:
  retries: 5    # Increase from 3
```

### False positives (service actually works but healthcheck fails)

**Cause:** Network issue, port not yet open, or check command wrong

**Debug:**
```bash
docker compose exec backend curl -v http://localhost:8000/health
```

**Fix:** Adjust check command or add timeout.

## Next Steps

- [Troubleshooting](troubleshooting.md) - Common issues
- [Production Setup](production-setup.md) - Production configuration
- [Configuration](configuration.md) - Configure healthchecks
