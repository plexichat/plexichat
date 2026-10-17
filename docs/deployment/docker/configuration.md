# Docker configuration

Configure Plexichat in Docker with environment variables and `.env` files.

## Overview

Configuration is resolved in this order, from highest to lowest priority:

1.  System environment variables
2.  `docker-compose.yml` `environment` entries
3.  `.env` file
4.  Built-in defaults in `src/config_defaults.py`

## Auto-generated configuration

The deploy scripts (`deploy.sh` and `deploy.ps1`) create these files on first run:

*   `.env` with secure passwords and connection strings
*   `config/docker-config.yaml` with backend service configuration
*   `docker/runtime/client-config.js` with client runtime configuration

These files persist across restarts. You do not need to edit them unless you want to customize the deployment.

To regenerate them, delete the files and run the deploy script again:

```bash
rm .env config/docker-config.yaml docker/runtime/client-config.js
curl -sSL https://plexichat.com/deploy.sh | bash
```

## Manual configuration

### Option 1: Edit the .env file

This is the recommended approach for local development.

```bash
umask 077
cp .env.example .env
chmod 600 .env
nano .env
```

Common settings:

```bash
# Database
POSTGRES_PASSWORD=your-secure-password
DB_POOL_MIN_CONNECTIONS=5
DB_POOL_MAX_CONNECTIONS=20

# Valkey
VALKEY_PASSWORD=your-valkey-password

# RustFS
S3_SECRET_KEY=your-rustfs-secret-key

# Logging
LOG_LEVEL=DEBUG
```

Apply changes:

```bash
docker compose down
docker compose up -d
```

### Option 2: Override in docker-compose.yml

Add entries under the `backend` `environment` section:

```yaml
services:
  backend:
    environment:
      LOG_LEVEL: DEBUG
      DB_POOL_MIN_CONNECTIONS: 10
```

### Option 3: Use system environment variables

```bash
export LOG_LEVEL=DEBUG
export DB_POOL_MAX_CONNECTIONS=50
docker compose up -d
```

## Environment variables reference

### Image versions

| Variable | Default | Purpose |
| --- | --- | --- |
| `BACKEND_VERSION` | `latest` | Tag for `backend` and `backup` images |
| `CLIENT_VERSION` | `latest` | Tag for the `client` Nginx image |
| `CI_REGISTRY_IMAGE` | `gitlab.plexichat.com/plexichat/plexichat` | Registry prefix for backend and backup images |

The Dockerfiles pin base images: `python:3.14.8-slim-trixie` for the backend, `nginx:stable-alpine` for the client, `postgres:18-alpine` for the database, `valkey/valkey:9.1.1-alpine` for Valkey, `rustfs/rustfs:1.0.0-rc.3` for storage, `rustfs/rc:v0.1.30` for bucket init, `clamav/clamav:1.5.4` for scanning, and `alpine:3.24` for cert generation.

### Database

| Variable | Default | Purpose |
| --- | --- | --- |
| `POSTGRES_HOST` | `db` | PostgreSQL hostname |
| `POSTGRES_PORT` | `5432` | PostgreSQL port |
| `POSTGRES_USER` | `plexichat` | PostgreSQL username |
| `POSTGRES_PASSWORD` | Required, no default | PostgreSQL password. Compose fails if unset |
| `POSTGRES_DBNAME` | `plexichat` | Database name |
| `POSTGRES_SSLMODE` | `disable` | SSL mode: `disable`, `allow`, `prefer`, or `require` |

### Connection pooling

| Variable | Default | Purpose |
| --- | --- | --- |
| `DB_POOL_MIN_CONNECTIONS` | `5` | Minimum idle connections |
| `DB_POOL_MAX_CONNECTIONS` | `20` | Maximum total connections |
| `DB_POOL_CONNECT_TIMEOUT` | `10` | Seconds to wait for a connection |
| `DB_POOL_MAX_IDLE_TIME` | `300` | Seconds before an idle connection is closed |
| `DB_POOL_VALIDATION_INTERVAL` | `60` | Seconds between health checks |
| `DB_POOL_ENABLE_VALIDATION` | `true` | Whether to run health checks |
| `DB_POOL_VALIDATION_QUERY` | `SELECT 1` | Query for health checks |

The built-in fallback in `src/config_defaults.py` uses `max_connections: 100`. The `.env.example` development default is `20`. See [Connection pooling](connection-pooling.md) for tuning guidance.

### Valkey

| Variable | Default | Purpose |
| --- | --- | --- |
| `VALKEY_HOST` | `valkey` | Valkey hostname |
| `VALKEY_PORT` | `6379` | Valkey port |
| `VALKEY_PASSWORD` | Required, no default | Valkey password. Compose fails if unset |
| `VALKEY_ENABLED` | `true` | Whether to enable Valkey |
| `VALKEY_CONNECTION_POOL` | `50` | Maximum Valkey connections |

Compose starts Valkey with `--requirepass "${VALKEY_PASSWORD}"` and its health check uses `REDISCLI_AUTH` so the password does not appear in the process list.

### RustFS and S3 storage

| Variable | Default | Purpose |
| --- | --- | --- |
| `S3_BUCKET` | `plexichat-media` | S3 bucket name |
| `S3_REGION` | `us-east-1` | S3 region |
| `S3_ENDPOINT` | `http://rustfs:9000` | S3 endpoint URL |
| `S3_ACCESS_KEY` | Required | S3 access key (also `RUSTFS_ACCESS_KEY`) |
| `S3_SECRET_KEY` | Required | S3 secret key (also `RUSTFS_SECRET_KEY`) |
| `S3_PUBLIC_URL` | Empty | Public URL for media access |
| `RUSTFS_ADDRESS` | `0.0.0.0:9000` | RustFS S3 listen address |
| `RUSTFS_CONSOLE_ADDRESS` | `0.0.0.0:9001` | RustFS console listen address |
| `RUSTFS_VOLUMES` | `/data/rustfs` | RustFS data path |
| `RUSTFS_CORS_ALLOWED_ORIGINS` | Empty | Allowed CORS origins. Keep empty to keep storage private |

RustFS runs as UID/GID `10001:10001`. The `rustfs-perms` init container fixes volume ownership before RustFS starts.

### Backup

| Variable | Default | Purpose |
| --- | --- | --- |
| `BACKUP_RESTIC_PASSWORD` | Required, no default | Restic repository password. Compose fails if unset |
| `BACKUP_SCHEDULE` | `0 2 * * *` | Cron schedule for supercronic |
| `BACKUP_KEEP_DAILY` | `7` | Number of daily snapshots to keep |
| `BACKUP_KEEP_WEEKLY` | `4` | Number of weekly snapshots to keep |
| `BACKUP_KEEP_MONTHLY` | `6` | Number of monthly snapshots to keep |
| `BACKUP_KEEP_YEARLY` | `0` | Number of yearly snapshots to keep |
| `BACKUP_INCLUDE_DB` | `true` | Whether to include a PostgreSQL dump |
| `BACKUP_INCLUDE_HOME` | `true` | Whether to include `plexichat_home` |
| `BACKUP_INCLUDE_MEDIA` | `true` | Whether to include `rustfs_data` |
| `BACKUP_SECONDARY_REPO` | Empty | Optional secondary Restic repository for off-site copies |
| `BACKUP_SECONDARY_PASSWORD` | Empty | Password for the secondary repository |
| `BACKUP_SUCCESS_HOOK` | Empty | Command to run after a successful backup |
| `BACKUP_FAILURE_HOOK` | Empty | Command to run after a failed backup |

### Search

| Variable | Default | Purpose |
| --- | --- | --- |
| `SEARCH_BACKEND` | `sqlite_fts5` | Search backend: `sqlite_fts5`, `meilisearch`, or `elasticsearch` |
| `SEARCH_MEILISEARCH_HOST` | `http://meilisearch:7700` | Meilisearch URL |
| `MEILISEARCH_API_KEY` | Empty | Meilisearch master key |
| `SEARCH_ELASTICSEARCH_HOSTS` | `http://elasticsearch:9200` | Elasticsearch hosts |
| `MEILISEARCH_MEM_LIMIT` | `512M` | Meilisearch memory limit |
| `ES_MEM_LIMIT` | `2G` | Elasticsearch memory limit |
| `ES_JAVA_OPTS` | `-Xms512m -Xmx512m` | Elasticsearch JVM options |

Enable Meilisearch with `--profile search-meilisearch` and Elasticsearch with `--profile search-elastic`. The default `sqlite_fts5` uses no extra containers.

### ClamAV

| Variable | Default | Purpose |
| --- | --- | --- |
| `SCANNER_ENABLED` | `true` | Whether the backend scans uploads |
| `SCANNER_HOST` | `clamav` | ClamAV hostname |
| `SCANNER_PORT` | `3310` | ClamAV TCP port |

ClamAV limits `StreamMaxLength`, `MaxScanSize`, and `MaxFileSize` are 600 MB. The `clamav-unofficial-sigs` sidecar updates signatures every 2 hours into the shared `clamav_db` volume.

### Encryption keys

| Variable | Default | Purpose |
| --- | --- | --- |
| `PLEXICHAT_SYSTEM_KEY` | Auto-generated | System encryption key. Do not share |
| `PLEXICHAT_MESSAGE_KEY` | Auto-generated | Message encryption key. Do not share |
| `PLEXICHAT_MEDIA_KEY` | Auto-generated | Media encryption key. Do not share |

### Email

| Variable | Default | Purpose |
| --- | --- | --- |
| `PLEXICHAT_SMTP_HOST` | Empty | SMTP server hostname |
| `PLEXICHAT_SMTP_PORT` | `587` | SMTP port |
| `PLEXICHAT_SMTP_USER` | Empty | SMTP username |
| `PLEXICHAT_SMTP_PASSWORD` | Empty | SMTP password |
| `PLEXICHAT_SMTP_USE_TLS` | `true` | Whether to use TLS for SMTP |

### Backend runtime

| Variable | Default | Purpose |
| --- | --- | --- |
| `PLEXICHAT_CONFIG` | `/app/config/docker-config.yaml` | Path to the backend config file |
| `PLEXICHAT_PUBLIC_SERVER_URL` | `http://localhost:8000` | Public URL for the backend |
| `NO_STRICT_CONFIG` | `false` | Whether to allow unknown config keys |
| `BACKEND_REPLICAS` | `1` | Intended replica count for scaling notes |
| `CI_REGISTRY_INSECURE` | `false` | Whether to allow an insecure registry |

### Logging and monitoring

| Variable | Default | Purpose |
| --- | --- | --- |
| `LOG_LEVEL` | `INFO` | Verbosity: `DEBUG`, `INFO`, `WARNING`, or `ERROR` |
| `MONITORING_ENABLED` | `true` | Whether to enable performance monitoring |
| `MONITORING_METRICS_ENABLED` | `true` | Whether to collect metrics |
| `MONITORING_LOG_INTERVAL` | `300` | Seconds between metrics log entries |

### Monitoring alert thresholds

| Variable | Default | Purpose |
| --- | --- | --- |
| `MONITORING_ALERT_CPU_THRESHOLD` | `80` | CPU alert threshold in percent |
| `MONITORING_ALERT_MEMORY_THRESHOLD` | `85` | Memory alert threshold in percent |
| `MONITORING_ALERT_DB_POOL_THRESHOLD` | `75` | Pool saturation alert in percent |
| `MONITORING_ALERT_QUERY_TIME_MS` | `5000` | Slow query alert in milliseconds |
| `MONITORING_ALERT_DB_ERRORS_PER_MINUTE` | `10` | Database error rate alert |
| `MONITORING_ALERT_API_RESPONSE_TIME_MS` | `2000` | API response time alert in milliseconds |
| `MONITORING_ALERT_ERROR_RATE_PERCENT` | `5` | API error rate alert in percent |
| `MONITORING_ALERT_ACTIVE_CONNECTIONS` | `1000` | Active connection count alert |

### AI moderation (optional)

| Variable | Default | Purpose |
| --- | --- | --- |
| `SHIELDSTRAL_MODEL` | `Shieldstral-1.0-3B-Q4_K_M.gguf` | Shieldstral model file |
| `SHIELDSTRAL_MMPROJ` | `mmproj-Shieldstral-1.0-3b-BF16.gguf` | Vision projector file |
| `SHIELDSTRAL_CONTEXT` | `4096` | Context size |
| `SHIELDSTRAL_GPU_LAYERS` | `0` | GPU layers to offload (99 on GPU hosts) |
| `SHIELDSTRAL_MEM_LIMIT` | `6G` | Container memory limit |
| `LLAMA_IMAGE` | `ghcr.io/ggml-org/llama.cpp:server-b10614` | llama.cpp image |

Enable with `docker compose --profile ai up -d`. The deploy script detects `nvidia-container-toolkit` and writes `docker-compose.gpu.yml` to enable GPU acceleration.

## Profile-specific behavior

`docker-compose.yml` uses Compose profiles, not `dev` or `prod` profiles:

*   **No profile (default):** PostgreSQL 18, Valkey 9.1.1, RustFS, ClamAV, backend, client, and backup. Search uses embedded `sqlite_fts5`.
*   **`--profile ai`:** Also starts `shieldstral` (llama.cpp) for local moderation.
*   **`--profile search` or `--profile search-meilisearch`:** Also starts `meilisearch` (`getmeili/meilisearch:v1.12`).
*   **`--profile search-elastic`:** Also starts `elasticsearch` (`docker.elastic.co/elasticsearch/elasticsearch:8.15.0`).

For database pool tuning, use `.env` values: `DB_POOL_MIN_CONNECTIONS=5` and `DB_POOL_MAX_CONNECTIONS=20` for development, `20` and `100`+ for production.

## Configuration precedence example

If you set the same variable in multiple places:

```bash
# .env file
DB_POOL_MAX_CONNECTIONS=25
```

```yaml
# docker-compose.yml
services:
  backend:
    environment:
      DB_POOL_MAX_CONNECTIONS: 30
```

```bash
# System environment
export DB_POOL_MAX_CONNECTIONS=40
```

The backend uses `40` because system environment variables have the highest priority.

## Common configuration scenarios

### High-concurrency production

```bash
POSTGRES_PASSWORD=your-strong-password
VALKEY_PASSWORD=your-valkey-password
S3_SECRET_KEY=your-strong-password
BACKUP_RESTIC_PASSWORD=your-restic-password

DB_POOL_MIN_CONNECTIONS=20
DB_POOL_MAX_CONNECTIONS=100
VALKEY_CONNECTION_POOL=100

LOG_LEVEL=INFO
MONITORING_ALERT_ACTIVE_CONNECTIONS=5000
```

### Low-resource development

```bash
DB_POOL_MIN_CONNECTIONS=2
DB_POOL_MAX_CONNECTIONS=10
VALKEY_CONNECTION_POOL=10

LOG_LEVEL=DEBUG
```

### External search

```bash
# Start with: docker compose --profile search-meilisearch up -d
SEARCH_BACKEND=meilisearch
MEILISEARCH_API_KEY=your-meilisearch-key
MEILISEARCH_MEM_LIMIT=512M
```

## Validation

Verify that configuration was applied:

```bash
docker compose exec backend env | grep POSTGRES
docker compose exec backend env | grep DB_POOL
docker compose exec backend cat /app/config/docker-config.yaml
docker compose config | grep -A 3 "depends_on"
```

## Related documentation

*   [Connection pooling](connection-pooling.md) for database tuning
*   [Healthchecks](healthchecks.md) for liveness and readiness probes
*   [Architecture](architecture.md) for service topology
