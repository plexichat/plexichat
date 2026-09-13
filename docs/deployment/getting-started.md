# Deployment guide

Install, configure, and run Plexichat in production. For subsystem-specific options, see the configuration pages linked in each section.

## Contents

- [System requirements](#system-requirements)
- [Installation](#installation)
- [Configuration](#configuration)
- [Security hardening](#security-hardening)
- [Service management](#service-management)
- [Verification](#verification)
- [Monitoring](#monitoring)
- [Backup and recovery](#backup-and-recovery)
- [Troubleshooting](#troubleshooting)

---

## System requirements

### Minimum

- CPU: 2 cores
- RAM: 4 GB
- Disk: 10 GB SSD
- OS: Linux (Ubuntu 22.04 or later, Debian 12 or later, CentOS Stream 9 or later)
- Python: 3.14 or later (the server image uses `python:3.14.6-slim-trixie` at `plexichat/Dockerfile:1`; 3.11 and 3.13 are not supported)

### Recommended for production

- CPU: 4 or more cores
- RAM: 8 GB or more (16 GB for large deployments or when ClamAV is enabled)
- Disk: 50 GB or more SSD, plus external storage for media
- Network: 1 Gbps or more for media-heavy deployments

### Service dependencies

- Database: PostgreSQL 18 (`postgres:18-alpine` in `plexichat/docker-compose.yml:25`) or SQLite for small and development deployments only
- Cache: Valkey 9.1 (`valkey/valkey:9.1.1-alpine` in `plexichat/docker-compose.yml:43`), required for production and for multi-worker scaling
- Reverse proxy: Nginx (bundled as `client` at `plexichat-client/Dockerfile:9`), or Caddy, Apache, or Traefik for TLS termination
- Optional: S3-compatible storage (RustFS 1.0 at `plexichat/docker-compose.yml:72`, AWS S3, or similar) for media

For detailed requirements, see [System requirements](requirements.md).

---

## Installation

### Step 1: Prepare the system

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3.14 python3.14-venv python3.14-dev git build-essential \
  libpq-dev libopus-dev libsrtp2-dev libvpx-dev ffmpeg curl

# Install PostgreSQL (if you run Postgres outside Docker)
sudo apt install -y postgresql postgresql-contrib

# Install Valkey (if you run Valkey outside Docker)
sudo apt install -y valkey-server
sudo systemctl enable valkey-server
sudo systemctl start valkey-server
```

The backend requires the same system libraries as the Docker image (`plexichat/Dockerfile:8`).

### Step 2: Clone the repository

```bash
git clone https://gitlab.plexichat.com/plexichat/plexichat.git
cd plexichat/plexichat
```

### Step 3: Create a virtual environment

```bash
python3.14 -m venv .venv
source .venv/bin/activate
```

### Step 4: Install dependencies

```bash
pip install --upgrade pip setuptools wheel
pip install --require-hashes -r requirements.txt
```

### Step 5: Create configuration

Create `config/config.yaml`. Plexichat merges this file over built-in defaults from `src/config_defaults.py` and generates a working default on first run if the file is missing. Use the [default configuration reference](../default-config.md) as the authoritative list of keys and defaults.

Do not copy `docs/default-config.md` as a config file. It is Markdown with code fences, not valid YAML.

Set these keys for production:

```yaml
application:
  environment: "production"

database:
  type: "postgres"
  postgres:
    host: "${POSTGRES_HOST:-localhost}"
    password: "${POSTGRES_PASSWORD}"
    sslmode: "require"

valkey:
  enabled: true
  password: "${VALKEY_PASSWORD}"

logging:
  level: "INFO"
  include_exception_details: false

api:
  debug: false
```

Then consult the subsystem pages for the features you use:

- [Database configuration](configuration/config-database.md) - PostgreSQL setup, pooling, migrations
- [Redis configuration](configuration/config-valkey.md) - Caching, sessions, pooling
- [Authentication configuration](configuration/config-authentication.md) - Password policies, 2FA, sessions, account deletion
- [Media configuration](configuration/config-media.md) - Storage backends, limits, processing, security
- [Voice configuration](configuration/config-voice.md) - SFU backends, STUN/TURN, NAT traversal
- [WebSocket configuration](configuration/config-websocket.md) - Gateway settings, compression, rate limits
- [Rate limiting configuration](configuration/config-rate-limiting.md) - Global, user, IP, bot, and webhook limits
- [API and server configuration](configuration/config-api.md) - CORS, trusted proxies, debug mode, TLS
- [Search configuration](configuration/config-search.md) - Search backends, indexing, result limits
- [Email configuration](configuration/config-email.md) - SMTP for verification and notifications
- [Embeds configuration](configuration/config-embeds.md) - URL previews and link embeds

### Step 6: Set environment variables

Plexichat supports `${VAR_NAME}` and `${VAR_NAME:-default}` interpolation in config files.

```bash
# Required for production encryption
export PLEXICHAT_SYSTEM_KEY="your_encryption_key"

# Database
export POSTGRES_PASSWORD="your_secure_password"

# Valkey
export VALKEY_PASSWORD="your_valkey_password"

# S3 (if you use external media storage)
export S3_BUCKET="your-bucket-name"
export S3_ACCESS_KEY="your_access_key"
export S3_SECRET_KEY="your_secret_key"
```

Store these values in a `.env` file, add `.env` to `.gitignore`, and set `chmod 600 .env`.

### First run

On first startup, Plexichat inspects `config/config.yaml` and generates any per-installation secrets that still use placeholder defaults:

- `media.signing_key` - If the value is still `CHANGE_THIS_SIGNING_KEY` or empty, the server generates a 32-byte secret with `secrets.token_hex(32)`, writes it to the `media:` section, and reuses it on later starts. Treat it as a production secret. Rotate it with `python main.py rotate-secrets --secret media-signing-key` if it is disclosed.
- `applications.webhook_signature_secret` - Generated with the same pattern during `initializer.initialize_modules`.

To supply your own value, set `media.signing_key` in `config/config.yaml` before the first run. The server skips generation when the key is already set.

---

## Docker deployment

For containerized deployment, use Docker or Docker Compose. This is the recommended approach for production.

### Quick start with Docker

```bash
# Build the image (uses python:3.14.6-slim-trixie)
docker build -f plexichat/Dockerfile -t plexichat:latest plexichat

# Run with environment variables
docker run -d \
  --name plexichat \
  -p 8000:8000 \
  -p 30000-30100:30000-30100/udp \
  -e PLEXICHAT_SYSTEM_KEY="your_encryption_key" \
  -e POSTGRES_PASSWORD="your_db_password" \
  -e VALKEY_PASSWORD="your_valkey_password" \
  -v $(pwd)/plexichat/config:/app/config \
  -v plexichat_home:/root/.plexichat \
  plexichat:latest
```

The image exposes `8000/TCP` and `30000-30100/UDP` for voice media (`plexichat/Dockerfile:40`) and runs as the non-root user `plexichat` (UID 10001 at `plexichat/Dockerfile:34`).

### Docker Compose (recommended)

Use the canonical `plexichat/docker-compose.yml:1`. Do not hand-write a minimal `compose.yml` unless you understand the required dependencies (`db`, `valkey`, `rustfs`, `rustfs-init`, `clamav`, `backend`, `client`).

Create your `.env` from `plexichat/.env.example:1`, then start:

```bash
BACKEND_VERSION=latest CLIENT_VERSION=latest docker compose -f plexichat/docker-compose.yml up -d
```

What the Compose file provides:

- `backend` on `8000/TCP` and `30000-30100/UDP` (`plexichat/docker-compose.yml:205`), health check `curl -sf http://localhost:8000/api/v1/ready` (`plexichat/docker-compose.yml:194`)
- `client` Nginx on `80` and `443` (`plexichat/docker-compose.yml:236`), proxying `/api/`, `/gateway`, `/docs`, and `/openapi.json` to `backend:8000` (`plexichat-client/docker/nginx/default.conf:12`)
- `db` on internal `5432`, `valkey` on internal `6379`, `rustfs` on internal `9000/9001` (exposed, not published), `clamav` on internal `3310`
- `backup` with restic, supercronic scheduling, and `/scripts/healthcheck.sh` liveness (`plexichat/docker-compose.yml:274`)

To override ports or add host mounts, create `docker-compose.override.yml` next to `plexichat/docker-compose.yml`.

### Environment variables

Create `plexichat/.env` from the example:

```bash
umask 077
cp plexichat/.env.example plexichat/.env
chmod 600 plexichat/.env
# Set strong values for POSTGRES_PASSWORD, VALKEY_PASSWORD, S3_ACCESS_KEY,
# S3_SECRET_KEY, and BACKUP_RESTIC_PASSWORD.
```

Or generate values (under `umask 077` so the secrets file is created `0600`
even if `chmod` below were skipped):

```bash
umask 077
echo "PLEXICHAT_SYSTEM_KEY=$(openssl rand -hex 32)" >> plexichat/.env
echo "POSTGRES_PASSWORD=$(openssl rand -base64 32)" >> plexichat/.env
echo "VALKEY_PASSWORD=$(openssl rand -base64 32)" >> plexichat/.env
chmod 600 plexichat/.env
```

### Database migrations in Docker

Plexichat runs pending migrations automatically on startup. To run them manually:

```bash
# Run migrations inside the backend container
docker compose -f plexichat/docker-compose.yml exec backend python -m src.core.migrations.cli apply_migrations

# Trigger a backup now
docker compose -f plexichat/docker-compose.yml exec backup /scripts/backup.sh run
```

### Health checks

The Docker setup includes health checks for core services (`plexichat/docker-compose.yml:35`, `plexichat/docker-compose.yml:194`):

```yaml
backend:
  healthcheck:
    test: ["CMD-SHELL", "curl -sf http://localhost:8000/api/v1/ready"]
    interval: 15s
    timeout: 5s
    retries: 3
    start_period: 10s
```

Use `/api/v1/ready` for readiness (returns `{"status":"ready"}` at `plexichat/src/api/routes/health.py:17`) and `/api/v1/health` for deep health including `cross_worker_listener` status.

---

## Security hardening

### Firewall

```bash
# Allow SSH
sudo ufw allow 22/tcp

# Allow HTTP/HTTPS when you terminate TLS at the reverse proxy
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp

# Allow the Plexichat backend directly only if you publish it outside Nginx
# Otherwise, block 8000 and rely on Nginx at 443
sudo ufw allow 8000/tcp   # Remove this rule if you keep backend internal
sudo ufw allow 30000:30100/udp  # Voice media (SFU)

# Enable firewall
sudo ufw enable
```

### Dedicated service user

```bash
sudo useradd -r -s /bin/false plexichat
sudo chown -R plexichat:plexichat /opt/plexichat
sudo chmod 750 /opt/plexichat
```

The Docker image already runs as `plexichat` (UID 10001). For bare-metal installs, run the process as the same unprivileged user.

### TLS through a reverse proxy (Nginx)

```nginx
server {
    listen 443 ssl http2;
    server_name chat.example.com;

    ssl_certificate /etc/ssl/certs/chat.example.com.crt;
    ssl_certificate_key /etc/ssl/private/chat.example.com.key;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;

    # Match the backend body cap (10 MB). Nginx default is 1 MB.
    client_max_body_size 10m;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }
}
```

The `/gateway` WebSocket path is served by the same application on the same port. The `location /` block covers it. You need a separate `/gateway` block only if you want different timeouts (see `plexichat-client/docker/nginx/default.conf:90`).

For full guidance, see [Security best practices](../security.md).

---

## Service management

### Systemd service

```ini
# /etc/systemd/system/plexichat.service
[Unit]
Description=Plexichat Server
After=network.target postgresql.service valkey.service

[Service]
Type=notify
User=plexichat
Group=plexichat
WorkingDirectory=/opt/plexichat
Environment="PATH=/opt/plexichat/.venv/bin"
ExecStart=/opt/plexichat/.venv/bin/python main.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

`main.py` starts Uvicorn with the configured host, port, and workers. You can also run `uvicorn src.api.app:create_app --factory --host 0.0.0.0 --port 8000 --workers 4` directly.

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable plexichat
sudo systemctl start plexichat
sudo systemctl status plexichat
```

### Worker count

Calculate workers from CPU cores: `workers = (2 * CPU cores) + 1`.

For multi-worker deployments, Valkey is required for shared session state and cross-worker events (`plexichat:events:cross_worker`).

### Horizontal scaling

For large deployments (>1,000 users), run multiple backend instances behind a load balancer with:

- Shared PostgreSQL 18 database
- Shared Valkey 9.1 instance
- Shared media storage (S3 or shared filesystem)
- Sticky sessions for WebSocket connections (`ip_hash` in Nginx)

See [Production setup](docker/production-setup.md#cross-worker-architecture).

---

## Verification

### Health checks

```bash
curl https://chat.example.com/api/v1/ready
# {"status":"ready"}

curl https://chat.example.com/api/v1/health
# {"status":"healthy","version":"a.1.0-131",...}

curl https://chat.example.com/api/v1/status
# Server state, version, uptime, maintenance info
```

Inside Docker:

```bash
curl -sf http://localhost:8000/api/v1/ready
curl -sf http://localhost:8000/api/v1/health | jq .
```

### API documentation

- Swagger UI: `/docs` (through Nginx at `https://chat.example.com/docs`, directly at `http://localhost:8000/docs`)
- ReDoc: `/redoc`
- OpenAPI spec: `/openapi.json`

### WebSocket test

```bash
wscat -c wss://chat.example.com/gateway
```

Direct to the backend:

```bash
curl -i -N -H "Connection: Upgrade" -H "Upgrade: websocket" \
  http://localhost:8000/gateway
# Expect 101 Switching Protocols
```

### Database and Valkey

```bash
psql -h localhost -U plexichat -d plexichat -c "SELECT 1;"
valkey-cli -h localhost -p 6379 ping
# Or inside Compose:
docker compose -f plexichat/docker-compose.yml exec db psql -U plexichat -d plexichat -c "SELECT 1;"
docker compose -f plexichat/docker-compose.yml exec valkey sh -c 'REDISCLI_AUTH="$VALKEY_PASSWORD" redis-cli ping'
```

---

## Monitoring

### Application metrics

Control telemetry in config:

```yaml
monitoring:
  enabled: true
  log_interval: 300
  metrics_enabled: true
  alert_thresholds:
    cpu_percent: 80
    memory_percent: 85
    db_pool_saturation_percent: 75
    query_time_ms: 5000
    db_errors_per_minute: 10
    api_response_time_ms: 2000
    error_rate_percent: 5
    active_connections: 1000
```

There is no built-in Prometheus `/metrics` endpoint. Scrape `/api/v1/health` and `/api/v1/ready` or consume `/api/v1/status` for uptime and version. Export custom metrics if you need Prometheus format.

### Key metrics to monitor

- Request rate and response times (P50, P95, P99)
- HTTP 5xx error rate
- Database connection pool saturation (`DB_POOL_*` in `plexichat/.env.example:28`)
- Valkey cache hit and miss rates
- WebSocket connection count and `cross_worker_listener` status from `/api/v1/health`
- CPU and memory utilization

### Logging

Logs are written to `~/.plexichat/logs/`:

- `plexichat.log` - Main application log
- `error.log` - Error-level messages

Configure rotation:

```bash
sudo cat > /etc/logrotate.d/plexichat << 'EOF'
/home/plexichat/.plexichat/logs/*.log {
    daily
    rotate 14
    compress
    delaycompress
    notifempty
    create 0640 plexichat plexichat
    sharedscripts
    postrotate
        systemctl reload-or-restart plexichat
    endscript
}
EOF
```

---

## Backup and recovery

### Database backups (PostgreSQL)

```bash
pg_dump -h localhost -U plexichat plexichat | gzip > /backups/plexichat-$(date +%Y%m%d).sql.gz
```

Automate with cron:

```bash
0 2 * * * pg_dump -h localhost -U plexichat plexichat | gzip > /backups/plexichat-$(date +\%Y\%m\%d).sql.gz
```

For Docker, use the `backup` service:

```bash
docker compose -f plexichat/docker-compose.yml exec backup /scripts/backup.sh run
docker compose -f plexichat/docker-compose.yml exec backup /scripts/backup.sh list
```

### Media backups

- **Local storage**: `rsync -av /home/plexichat/.plexichat/media/ /backups/media/`
- **S3 or RustFS**: Enable versioning and lifecycle policies in the bucket
- **Docker**: The `backup` service includes `plexichat_home` and `rustfs_data` when `BACKUP_INCLUDE_HOME=true` and `BACKUP_INCLUDE_MEDIA=true` (`plexichat/docker-compose.yml:240`)

### Configuration backups

```bash
cp config/config.yaml /backups/config-$(date +%Y%m%d).yaml
cp plexichat/.env /backups/env-$(date +%Y%m%d).bak
chmod 600 /backups/env-*.bak
```

### Recovery procedure

1. Stop the Plexichat service.
2. Restore the database from backup.
3. Restore the media directory (if you use local storage).
4. Restore configuration and `.env`.
5. Start the service.
6. Verify `curl -sf http://localhost:8000/api/v1/ready` returns `{"status":"ready"}`.

---

## Troubleshooting

### Server does not start

```bash
sudo systemctl status plexichat
sudo journalctl -u plexichat -n 100
tail -100 ~/.plexichat/logs/plexichat.log
```

Common causes: database connection failure (check `POSTGRES_*` in `.env`), Valkey connection failure (check `VALKEY_PASSWORD`), config YAML syntax error, or port already in use (8000/TCP or 30000–30100/UDP).

### Database connection errors

Verify PostgreSQL is running, credentials match, and `sslmode` is correct for your environment. Inside Docker, check:

```bash
docker compose -f plexichat/docker-compose.yml exec backend env | grep POSTGRES
docker compose -f plexichat/docker-compose.yml logs db --tail 50
```

### WebSocket issues

Verify the reverse proxy sets `Upgrade` and `Connection: upgrade` and uses long timeouts (`plexichat-client/docker/nginx/default.conf:90`). Check firewall rules for 443 and for 30000–30100/UDP if you use voice.

### Get help

1. Collect relevant logs.
2. Document system configuration and version (`curl -sf http://localhost:8000/api/v1/health`).
3. Check the [configuration overview](../configuration.md) and subsystem pages.
4. Verify all dependencies are healthy (`docker compose ps`).

---

## Related documentation

- [Configuration overview](../configuration.md) - Config discovery and module guides
- [Default configuration reference](../default-config.md) - Every key with defaults
- [Authentication configuration](configuration/config-authentication.md) - Password, 2FA, sessions, deletion
- [Database configuration](configuration/config-database.md) - PostgreSQL and SQLite setup
- [Valkey configuration](configuration/config-valkey.md) - Caching and sessions
- [Media configuration](configuration/config-media.md) - Storage and processing
- [Voice configuration](configuration/config-voice.md) - Voice and video setup
- [Docker deployment](docker/index.md) - Containerized deployment
- [Security best practices](../security.md) - Production security
- [Performance guide](../performance.md) - Optimization
