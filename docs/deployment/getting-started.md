# Deployment Guide

This guide covers installing, configuring, and running Plexichat in production. For detailed per-subsystem configuration, see the dedicated configuration pages linked throughout.

## Table of Contents

- [System Requirements](#system-requirements)
- [Installation](#installation)
- [Configuration](#configuration)
- [Security Hardening](#security-hardening)
- [Service Management](#service-management)
- [Verification](#verification)
- [Monitoring](#monitoring)
- [Backup and Recovery](#backup-and-recovery)
- [Troubleshooting](#troubleshooting)

---

## System Requirements

### Minimum

- CPU: 2 cores
- RAM: 2GB
- Disk: 10GB SSD
- OS: Linux (Ubuntu 20.04+, Debian 11+, CentOS Stream 9+)
- Python: **3.11 or later** (the server uses 3.11+ language features; 3.10 will not work)

### Recommended for Production

- CPU: 4+ cores
- RAM: 8GB+ (16GB for large deployments)
- Disk: 50GB+ SSD (with external storage for media)
- Network: 1Gbps+ for media-heavy deployments

### Service Dependencies

- Database: PostgreSQL 12+ (recommended) or SQLite (small/dev deployments only)
- Cache: Redis 6+ (strongly recommended for production)
- Reverse Proxy: nginx, Apache, Caddy, or Traefik (for TLS termination)
- Optional: S3-compatible storage (MinIO, AWS S3, etc.) for media

For detailed requirements, see [System Requirements](requirements.md).

---

## Installation

### Step 1: Prepare the System

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3.11 python3.11-pip python3.11-venv git build-essential

# Install PostgreSQL (if using PostgreSQL)
sudo apt install -y postgresql postgresql-contrib

# Install Redis (if using Redis)
sudo apt install -y redis-server
sudo systemctl enable redis-server
sudo systemctl start redis-server
```

### Step 2: Clone the Repository

```bash
git clone https://gitlab.plexichat.com/plexichat/plexichat.git
cd plexichat
```

### Step 3: Create Virtual Environment

```bash
python3.11 -m venv .venv
source .venv/bin/activate
```

### Step 4: Install Dependencies

```bash
pip install --upgrade pip setuptools wheel
pip install -r requirements.txt
```

### Step 5: Create Configuration

```bash
mkdir -p config
```

Create `config/config.yaml` with the settings appropriate for your environment. The server auto-generates a working default config on first run if none is found. For a production config, use the [Default Configuration Reference](../default-config.md) as your guide -- it lists every key, its default value, and what it controls.

**Do not** copy `docs/default-config.md` as your config file -- it is a Markdown document with code fences, not valid YAML.

At minimum, set these for production:

```yaml
application:
  environment: "production"

database:
  type: "postgres"
  postgres:
    host: "${POSTGRES_HOST:-localhost}"
    password: "${POSTGRES_PASSWORD}"
    sslmode: "require"

redis:
  enabled: true
  password: "${REDIS_PASSWORD}"

logging:
  level: "INFO"
  include_exception_details: false

api:
  debug: false
```

Then consult the dedicated config pages for each subsystem you need:

- [Database Configuration](configuration/config-database.md) -- PostgreSQL setup, connection pooling, migrations
- [Redis Configuration](configuration/config-redis.md) -- caching, sessions, connection pooling
- [Authentication Configuration](configuration/config-authentication.md) -- password policies, 2FA, sessions, account deletion
- [Media Configuration](configuration/config-media.md) -- storage backends, file limits, processing, security
- [Voice Configuration](configuration/config-voice.md) -- SFU backends, STUN/TURN, NAT traversal
- [WebSocket Configuration](configuration/config-websocket.md) -- gateway settings, compression, rate limits
- [Rate Limiting Configuration](configuration/config-rate-limiting.md) -- global, user, IP, bot, webhook limits
- [API and Server Configuration](configuration/config-api.md) -- CORS, trusted proxies, debug mode, TLS
- [Search Configuration](configuration/config-search.md) -- search backends, indexing, result limits
- [Email Configuration](configuration/config-email.md) -- SMTP for email verification and notifications
- [Embeds Configuration](configuration/config-embeds.md) -- URL previews, link embeds

### Step 6: Set Environment Variables

Plexichat supports `${VAR_NAME}` and `${VAR_NAME:-default}` interpolation in config files.

```bash
# Required for production encryption
export PLEXICHAT_SYSTEM_KEY="your_encryption_key"

# Database
export POSTGRES_PASSWORD="your_secure_password"

# Redis
export REDIS_PASSWORD="your_redis_password"

# S3 (if using external media storage)
export S3_BUCKET="your-bucket-name"
export S3_ACCESS_KEY="your_access_key"
export S3_SECRET_KEY="your_secret_key"
```

Create a `.env` file (add to `.gitignore`) and `chmod 600 .env`.

----

## Docker Deployment

For containerized deployment, use Docker or Docker Compose. This is the recommended approach for production environments.

### Quick Start with Docker

```bash
# Build the Docker image
docker build -t plexichat:latest .

# Run with environment variables
docker run -d \
  --name plexichat \
  -p 8000:8000 \
  -e PLEXICHAT_SYSTEM_KEY="your_encryption_key" \
  -e POSTGRES_PASSWORD="your_db_password" \
  -e REDIS_PASSWORD="your_redis_password" \
  -v $(pwd)/config:/app/config \
  -v plexichat_data:/data \
  plexichat:latest
```

### Docker Compose (Recommended)

Create a `compose.yml` file:

```yaml
services:
  plexichat:
    build: .
    container_name: plexichat
    ports:
      - "8000:8000"
    environment:
      - PLEXICHAT_SYSTEM_KEY=${PLEXICHAT_SYSTEM_KEY}
      - POSTGRES_PASSWORD=${POSTGRES_PASSWORD}
      - REDIS_PASSWORD=${REDIS_PASSWORD}
    volumes:
      - ./config:/app/config
      - plexichat_data:/data
    depends_on:
      - postgres
      - redis
    restart: unless-stopped
    networks:
      - plexichat_network

  postgres:
    image: postgres:15-alpine
    container_name: plexichat_postgres
    environment:
      - POSTGRES_USER=plexichat
      - POSTGRES_PASSWORD=${POSTGRES_PASSWORD}
      - POSTGRES_DB=plexichat
    volumes:
      - postgres_data:/var/lib/postgresql/data
    restart: unless-stopped
    networks:
      - plexichat_network

  redis:
    image: redis:7-alpine
    container_name: plexichat_redis
    command: redis-server --requirepass ${REDIS_PASSWORD}
    volumes:
      - redis_data:/data
    restart: unless-stopped
    networks:
      - plexichat_network

  nginx:
    image: nginx:alpine
    container_name: plexichat_nginx
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./nginx.conf:/etc/nginx/nginx.conf:ro
      - ./ssl:/etc/nginx/ssl:ro
    depends_on:
      - plexichat
    restart: unless-stopped
    networks:
      - plexichat_network

volumes:
  plexichat_data:
  postgres_data:
  redis_data:

networks:
  plexichat_network:
    driver: bridge
```

### Environment Variables

Create a `.env` file:

```bash
PLEXICHAT_SYSTEM_KEY=$(openssl rand -hex 32)
POSTGRES_PASSWORD=$(openssl rand -base64 32)
REDIS_PASSWORD=$(openssl rand -base64 32)
```

### Database Migrations in Docker

Plexichat automatically runs pending migrations on startup. To run them manually:

```bash
# Run migrations
docker compose exec plexichat python -m src.core.migrations.cli apply_migrations

# Create a backup
docker compose exec postgres pg_dump -U plexichat plexichat > backup.sql
```

### Health Checks

The Docker setup includes health checks for all services:

```yaml
healthcheck:
  test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
  interval: 30s
  timeout: 10s
  retries: 3
  start_period: 40s
```

----

## Security Hardening

### Firewall

```bash
# Allow SSH
sudo ufw allow 22/tcp

# Allow HTTP/HTTPS (if terminating TLS at reverse proxy)
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp

# Allow Plexichat port (if not using reverse proxy)
sudo ufw allow 8000/tcp

# Enable firewall
sudo ufw enable
```

### Dedicated Service User

```bash
sudo useradd -r -s /bin/false plexichat
sudo chown -R plexichat:plexichat /opt/plexichat
sudo chmod 750 /opt/plexichat
```

### TLS via Reverse Proxy (nginx)

```nginx
server {
    listen 443 ssl http2;
    server_name chat.example.com;

    ssl_certificate /etc/ssl/certs/chat.example.com.crt;
    ssl_certificate_key /etc/ssl/private/chat.example.com.key;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 86400;
    }
}
```

**Important**: The `/gateway` WebSocket path is served by the same application on the same port. The `location /` block above already covers it. You do not need a separate location block unless you want different proxy settings for WebSocket traffic.

For full security guidance, see [Security Best Practices](../security.md).

---

## Service Management

### Systemd Service

```ini
# /etc/systemd/system/plexichat.service
[Unit]
Description=Plexichat Server
After=network.target postgresql.service redis.service

[Service]
Type=notify
User=plexichat
Group=plexichat
WorkingDirectory=/opt/plexichat
Environment="PATH=/opt/plexichat/.venv/bin"
ExecStart=/opt/plexichat/.venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000 --workers 4
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable plexichat
sudo systemctl start plexichat
sudo systemctl status plexichat
```

### Worker Count

Calculate workers based on CPU cores: `workers = (2 * CPU cores) + 1`

For multi-worker deployments, Redis is required for shared session state.

### Horizontal Scaling

For large deployments (>1,000 users), run multiple instances behind a load balancer with:
- Shared PostgreSQL database
- Shared Redis instance
- Shared media storage (S3 or shared filesystem)
- Sticky sessions for WebSocket connections

---

## Verification

### Health Checks

```bash
curl https://chat.example.com/health
# {"status": "ok"}

curl https://chat.example.com/api/v1/version
# {"version": "a.1.0-51", "minimum_client_version": "a.1.0-0"}

curl https://chat.example.com/api/v1/status
# Server state, version, uptime, maintenance info
```

### API Documentation

- Swagger UI: `/docs`
- ReDoc: `/redoc`
- OpenAPI spec: `/openapi.json`

### WebSocket Test

```bash
wscat -c wss://chat.example.com/gateway
```

### Database and Redis

```bash
psql -h localhost -U plexichat -d plexichat -c "SELECT 1;"
redis-cli ping
```

---

## Monitoring

### Application Metrics

The `monitoring` config section controls built-in telemetry:

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

**Note**: There is no built-in Prometheus endpoint. If you need Prometheus metrics, expose the `/api/v1/status` and `/health` endpoints to your Prometheus scraper and use a custom metrics exporter.

### Key Metrics to Monitor

- Request rate and response times (P50, P95, P99)
- HTTP 5xx error rate
- Database connection pool saturation
- Redis cache hit/miss rates
- WebSocket connection count
- CPU and memory utilization

### Logging

Logs are written to `~/.plexichat/logs/`:

- `plexichat.log` -- main application log
- `error.log` -- error-level messages

Configure log rotation:

```bash
sudo cat > /etc/logrotate.d/plexichat << EOF
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

## Backup and Recovery

### Database Backups (PostgreSQL)

```bash
pg_dump -h localhost -U plexichat plexichat | gzip > /backups/plexichat-$(date +%Y%m%d).sql.gz
```

Automate with cron:

```bash
0 2 * * * pg_dump -h localhost -U plexichat plexichat | gzip > /backups/plexichat-$(date +\%Y\%m\%d).sql.gz
```

### Media Backups

- **Local storage**: `rsync -av /home/plexichat/.plexichat/media/ /backups/media/`
- **S3**: Enable versioning and lifecycle policies in your bucket settings

### Configuration Backups

```bash
cp config/config.yaml /backups/config-$(date +%Y%m%d).yaml
```

### Recovery Procedure

1. Stop Plexichat service
2. Restore database from backup
3. Restore media directory (if using local storage)
4. Restore configuration
5. Start Plexichat service
6. Verify health check passes

---

## Troubleshooting

### Server Won't Start

```bash
sudo systemctl status plexichat
sudo journalctl -u plexichat -n 100
tail -100 ~/.plexichat/logs/plexichat.log
```

Common causes: database connection failure, Redis connection failure, config syntax error, port already in use.

### Database Connection Errors

Verify PostgreSQL is running, credentials in config match, and SSL settings are correct.

### WebSocket Issues

Verify reverse proxy WebSocket configuration (Upgrade headers), increase proxy timeouts, and check firewall rules.

### Getting Help

1. Collect relevant logs
2. Document system configuration
3. Check the [Configuration Overview](../configuration.md) and per-subsystem config pages
4. Verify all dependencies are running

---

## Related Documentation

- [Configuration Overview](../configuration.md) -- config discovery and module-specific guides
- [Default Configuration Reference](../default-config.md) -- every key with defaults
- [Authentication Configuration](configuration/config-authentication.md) -- password, 2FA, sessions, deletion
- [Database Configuration](configuration/config-database.md) -- PostgreSQL/SQLite setup
- [Redis Configuration](configuration/config-redis.md) -- caching and sessions
- [Media Configuration](configuration/config-media.md) -- storage and processing
- [Voice Configuration](configuration/config-voice.md) -- voice/video setup
- [Security Best Practices](../security.md) -- production security
- [Performance Guide](../performance.md) -- optimization
