# Docker Configuration

How to configure Plexichat in Docker using environment variables and .env files.

## Overview

Configuration flows through multiple sources in this order (highest to lowest priority):

1. System environment variables
2. `docker-compose.yml` `environment:` section
3. `.env` file (created by the deploy script or manually)
4. Built-in defaults

## Auto-Generated Configuration

The standalone deploy scripts (`deploy.sh` / `deploy.ps1`) automatically create:

- `.env` - Cryptographically secure keys and connection strings
- `config/docker-config.yaml` - Backend service configuration
- `docker/runtime/client-config.js` - Client runtime configuration

These are generated once and persist across restarts. You typically don't need to modify them.

To regenerate, delete the files and re-run the deploy script:
```bash
rm .env config/docker-config.yaml docker/runtime/client-config.js
curl -sSL https://plexichat.com/deploy.sh | bash
```

## Manual Configuration

### Option 1: .env File (Recommended for Local Development)

Copy the example and customize:
```bash
cp .env.example .env
nano .env
```

Set your values. Common options:

```bash
# Database
POSTGRES_PASSWORD=your-secure-password
DB_POOL_MIN_CONNECTIONS=5
DB_POOL_MAX_CONNECTIONS=20

# Redis
REDIS_PASSWORD=your-redis-password

# MinIO
MINIO_ROOT_PASSWORD=your-minio-password

# Logging
LOG_LEVEL=DEBUG
```

Restart services to apply:
```bash
docker compose down
docker compose up
```

### Option 2: Command-Line Overrides

Override in `docker-compose.yml`:

```yaml
backend:
  environment:
    LOG_LEVEL: DEBUG
    DB_POOL_MIN_CONNECTIONS: 10
```

### Option 3: System Environment Variables

Set before starting compose:

```bash
export LOG_LEVEL=DEBUG
export DB_POOL_MAX_CONNECTIONS=50
docker compose up
```

## Environment Variables Reference

### Database Configuration

| Variable | Default | Purpose |
|----------|---------|---------|
| `POSTGRES_HOST` | `db` | PostgreSQL hostname |
| `POSTGRES_PORT` | `5432` | PostgreSQL port |
| `POSTGRES_USER` | `plexichat` | PostgreSQL username |
| `POSTGRES_PASSWORD` | auto-generated | PostgreSQL password (CHANGE THIS) |
| `POSTGRES_DBNAME` | `plexichat` | Database name |
| `POSTGRES_SSLMODE` | `disable` | SSL mode (disable, allow, prefer, require) |

### Connection Pooling

| Variable | Default | Purpose |
|----------|---------|---------|
| `DB_POOL_MIN_CONNECTIONS` | 5 | Minimum idle connections |
| `DB_POOL_MAX_CONNECTIONS` | 20 | Maximum total connections |
| `DB_POOL_CONNECT_TIMEOUT` | 10 | Connection timeout in seconds |
| `DB_POOL_MAX_IDLE_TIME` | 300 | Idle connection timeout in seconds |
| `DB_POOL_VALIDATION_INTERVAL` | 60 | Connection validation check interval |
| `DB_POOL_ENABLE_VALIDATION` | true | Enable connection health checks |
| `DB_POOL_VALIDATION_QUERY` | SELECT 1 | Query for health checks |

See [Connection Pooling](connection-pooling.md) for tuning guidance.

### Redis Configuration

| Variable | Default | Purpose |
|----------|---------|---------|
| `REDIS_HOST` | `redis` | Redis hostname |
| `REDIS_PORT` | `6379` | Redis port |
| `REDIS_PASSWORD` | auto-generated | Redis password (CHANGE THIS) |
| `REDIS_ENABLED` | true | Enable Redis caching |
| `REDIS_CONNECTION_POOL` | 50 | Max Redis connections |

### MinIO / S3 Storage

| Variable | Default | Purpose |
|----------|---------|---------|
| `MINIO_ROOT_USER` | `plexichat` | MinIO admin username |
| `MINIO_ROOT_PASSWORD` | auto-generated | MinIO admin password (CHANGE THIS) |
| `S3_BUCKET` | `plexichat-media` | S3 bucket name |
| `S3_REGION` | `us-east-1` | S3 region |
| `S3_ENDPOINT` | `http://minio:9000` | S3 endpoint URL |
| `S3_ACCESS_KEY` | `plexichat` | S3 access key |
| `S3_SECRET_KEY` | auto-generated | S3 secret key |
| `S3_PUBLIC_URL` | empty | Public URL for media access |

### Encryption Keys

| Variable | Default | Purpose |
|----------|---------|---------|
| `PLEXICHAT_SYSTEM_KEY` | auto-generated | System encryption key (DO NOT SHARE) |
| `PLEXICHAT_MESSAGE_KEY` | auto-generated | Message encryption key (DO NOT SHARE) |
| `PLEXICHAT_MEDIA_KEY` | auto-generated | Media encryption key (DO NOT SHARE) |

### Email (Optional)

| Variable | Default | Purpose |
|----------|---------|---------|
| `PLEXICHAT_SMTP_HOST` | empty | SMTP server hostname |
| `PLEXICHAT_SMTP_PORT` | `587` | SMTP port |
| `PLEXICHAT_SMTP_USER` | empty | SMTP username |
| `PLEXICHAT_SMTP_PASSWORD` | empty | SMTP password |
| `PLEXICHAT_SMTP_USE_TLS` | true | Use TLS for SMTP |

### Logging & Monitoring

| Variable | Default | Purpose |
|----------|---------|---------|
| `LOG_LEVEL` | `INFO` | Log verbosity (DEBUG, INFO, WARNING, ERROR) |
| `MONITORING_ENABLED` | true | Enable performance monitoring |
| `MONITORING_METRICS_ENABLED` | true | Enable metrics collection |
| `MONITORING_LOG_INTERVAL` | 300 | Metrics log interval in seconds |

### Monitoring Alerts

| Variable | Default | Purpose |
|----------|---------|---------|
| `MONITORING_ALERT_CPU_THRESHOLD` | 80 | CPU alert threshold (%) |
| `MONITORING_ALERT_MEMORY_THRESHOLD` | 85 | Memory alert threshold (%) |
| `MONITORING_ALERT_DB_POOL_THRESHOLD` | 75 | Pool saturation alert (%) |
| `MONITORING_ALERT_QUERY_TIME_MS` | 5000 | Query time alert (ms) |
| `MONITORING_ALERT_DB_ERRORS_PER_MINUTE` | 10 | DB error rate alert |
| `MONITORING_ALERT_API_RESPONSE_TIME_MS` | 2000 | API response time alert (ms) |
| `MONITORING_ALERT_ERROR_RATE_PERCENT` | 5 | API error rate alert (%) |
| `MONITORING_ALERT_ACTIVE_CONNECTIONS` | 1000 | Active connection alert count |

## Profile-Specific Defaults

### Development Profile (`--profile dev`)
```
LOG_LEVEL=DEBUG
DB_POOL_MIN_CONNECTIONS=5
DB_POOL_MAX_CONNECTIONS=20
MONITORING_ENABLED=true
```

### Production Profile (`--profile prod`)
```
LOG_LEVEL=INFO
DB_POOL_MIN_CONNECTIONS=20
DB_POOL_MAX_CONNECTIONS=100
MONITORING_ENABLED=true
MONITORING_METRICS_ENABLED=true
```

### Test Profile (`--profile test`)
Uses SQLite instead of PostgreSQL, no Redis, minimal logging.

## Configuration Precedence Example

If you set:
```bash
# .env file
DB_POOL_MAX_CONNECTIONS=25

# docker-compose.yml
backend:
  environment:
    DB_POOL_MAX_CONNECTIONS: 30

# System environment
export DB_POOL_MAX_CONNECTIONS=40
```

Result: Backend will use **40** (system env wins)

## Common Configuration Scenarios

### High-Concurrency Production

```bash
POSTGRES_PASSWORD=your-strong-password
REDIS_PASSWORD=your-strong-password
MINIO_ROOT_PASSWORD=your-strong-password

DB_POOL_MIN_CONNECTIONS=20
DB_POOL_MAX_CONNECTIONS=150
REDIS_CONNECTION_POOL=100

LOG_LEVEL=INFO
MONITORING_ALERT_ACTIVE_CONNECTIONS=5000
```

### Low-Resource Development

```bash
DB_POOL_MIN_CONNECTIONS=2
DB_POOL_MAX_CONNECTIONS=10
REDIS_CONNECTION_POOL=10

LOG_LEVEL=DEBUG
```

### Testing / CI

Use `--profile test` profile (SQLite, minimal services)

## Validation

Check configuration was applied:
```bash
docker compose exec backend env | grep POSTGRES
docker compose exec backend env | grep DB_POOL
```

View active backend config:
```bash
docker compose exec backend cat config/docker-config.yaml
```

## Documentation

For more details on specific subsystems:

- [Connection Pooling](connection-pooling.md) - Database connection tuning
- [Production Setup](production-setup.md) - Security and hardening
- [Troubleshooting](troubleshooting.md) - Configuration issues

## Next Steps

- [Quick Start](quick-start.md) - Get running
- [Development Workflow](development-workflow.md) - Hot reload and testing
- [Production Setup](production-setup.md) - Harden for production
