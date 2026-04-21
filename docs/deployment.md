# Deployment Guide

This guide provides comprehensive operational guidance for deploying and running Plexichat in production environments. It covers installation, configuration, security, scaling, monitoring, and maintenance procedures for production deployments.

## Table of Contents

- [System Requirements](#system-requirements)
- [Installation](#installation)
- [Configuration](#configuration)
- [Security Hardening](#security-hardening)
- [Deployment Patterns](#deployment-patterns)
- [Verification](#verification)
- [Monitoring](#monitoring)
- [Backup and Recovery](#backup-and-recovery)
- [Maintenance](#maintenance)
- [Troubleshooting](#troubleshooting)

---

## System Requirements

### Minimum Requirements

- **CPU**: 2 cores
- **RAM**: 2GB
- **Disk**: 10GB SSD
- **OS**: Linux (Ubuntu 20.04+, Debian 11+, CentOS Stream 9+)
- **Python**: 3.10 or later

### Recommended for Production

- **CPU**: 4+ cores
- **RAM**: 8GB+ (16GB for large deployments)
- **Disk**: 50GB+ SSD (with external storage for media)
- **OS**: Linux with LTS support
- **Network**: 1Gbps+ for media-heavy deployments

### Service Dependencies

- **Database**: PostgreSQL 12+ (recommended) or SQLite (small deployments)
- **Cache**: Redis 6+ (strongly recommended for production)
- **Reverse Proxy**: nginx, Apache, or similar (for TLS termination)
- **Optional**: S3-compatible storage (AWS S3, MinIO, etc.) for media

---

## Installation

### Step 1: Prepare the System

```bash
# Update system packages
sudo apt update && sudo apt upgrade -y

# Install Python and build dependencies
sudo apt install -y python3 python3-pip python3-venv git build-essential

# Install PostgreSQL (if using PostgreSQL)
sudo apt install -y postgresql postgresql-contrib

# Install Redis (if using Redis)
sudo apt install -y redis-server
sudo systemctl enable redis-server
sudo systemctl start redis-server
```

### Step 2: Clone the Repository

```bash
# Clone with submodules
git clone --recurse-submodules https://gitlab.plexichat.com/plexichat/plexichat.git
cd plexichat
```

### Step 3: Create Virtual Environment

```bash
# Create virtual environment
python3 -m venv .venv

# Activate virtual environment
source .venv/bin/activate
```

### Step 4: Install Dependencies

```bash
# Upgrade pip and build tools
pip install --upgrade pip setuptools wheel

# Install application dependencies
pip install -r requirements.txt
```

### Step 5: Create Configuration Directory

```bash
# Create configuration directory
mkdir -p config

# Copy default configuration
cp docs/default-config.md config/config.yaml

# Edit configuration for your environment
nano config/config.yaml
```

---

## Configuration

Configuration is loaded from multiple sources in priority order:

1. Command line argument: `--config /path/to/config.yaml`
2. Environment variable: `PLEXICHAT_CONFIG=/path/to/config.yaml`
3. Auto-discovered files:
   - `./config/config.yaml` (project directory)
   - `~/.plexichat/config/config.yaml` (home directory)
4. Built-in defaults (see [Default Configuration Reference](default-config.md))

### Essential Configuration for Production

#### Database Configuration

For production deployments, use PostgreSQL:

```yaml
database:
  type: "postgres"
  postgres:
    host: "localhost"
    port: 5432
    user: "plexichat"
    password: ""  # Use environment variable
    dbname: "plexichat"
    sslmode: "require"
  connection_pool:
    min_connections: 5
    max_connections: 50
    connect_timeout: 10
```

See [Database Configuration](config-database.md) for detailed guidance on PostgreSQL vs SQLite selection, connection pooling, and scaling.

#### Redis Configuration

Enable Redis for production deployments:

```yaml
redis:
  enabled: true
  host: "localhost"
  port: 6379
  password: ""  # Set a strong password
  ssl: false  # Enable if Redis is on remote server
  connection_pool:
    max_connections: 50
    timeout: 5
```

See [Redis Configuration](config-redis.md) for detailed guidance on Redis setup, connection pooling, and scaling strategies.

#### Authentication Configuration

Secure authentication settings:

```yaml
authentication:
  password:
    min_length: 12
    require_uppercase: true
    require_lowercase: true
    require_digit: true
    require_special: true
  sessions:
    expire_hours: 168  # 7 days
    max_per_user: 10
    extend_on_activity: true
  totp:
    enabled: true
  account_deletion:
    enabled: true
    grace_period_days: 30
```

See [Authentication Configuration](config-authentication.md) for detailed guidance on password policies, 2FA, sessions, and account deletion settings.

#### Media Configuration

Configure external storage for production:

```yaml
media:
  storage_backend: "s3"
  s3:
    bucket: "your-bucket-name"
    region: "us-east-1"
    access_key_id: ""  # Use environment variable
    secret_access_key: ""  # Use environment variable
  max_file_size: 104857600  # 100MB
  max_total_size_per_user: 10737418240  # 10GB
```

See [Media Configuration](config-media.md) for detailed guidance on storage backends, file limits, processing, and security.

#### Voice Configuration (Optional)

If enabling voice/video features:

```yaml
voice:
  enabled: true
  sfu_backend: "mediasoup"  # default; "aiortc" available for lightweight deployments
  stun_urls:
    - "stun:stun.l.google.com:19302"
  max_participants_per_channel: 25
```

See [Voice Configuration](config-voice.md) for detailed guidance on SFU backends, STUN/TURN servers, and NAT traversal.

### Environment Variables

Plexichat supports environment variable interpolation in configuration files using the `${VAR_NAME}` syntax.

#### Interpolation Syntax

- **Required**: `${VAR_NAME}` - Fails if not set
- **Optional with default**: `${VAR_NAME:-default}` - Uses default if not set

#### Example Configuration

```yaml
database:
  postgres:
    host: "${POSTGRES_HOST:-localhost}"
    password: "${POSTGRES_PASSWORD}"
    sslmode: "${POSTGRES_SSLMODE:-require}"

redis:
  host: "${REDIS_HOST:-localhost}"
  password: "${REDIS_PASSWORD}"

media:
  s3:
    bucket: "${S3_BUCKET}"
    access_key_id: "${S3_ACCESS_KEY_ID}"
    secret_access_key: "${S3_SECRET_ACCESS_KEY}"
```

#### Setting Environment Variables

```bash
# Database
export POSTGRES_PASSWORD="your_secure_password"
export POSTGRES_HOST="localhost"
export POSTGRES_SSLMODE="require"

# Redis
export REDIS_PASSWORD="your_redis_password"

# S3
export S3_BUCKET="your-bucket-name"
export S3_ACCESS_KEY_ID="your_access_key"
export S3_SECRET_ACCESS_KEY="your_secret_key"

# Bootstrap encryption (required before config loads)
export PLEXICHAT_SYSTEM_KEY="your_encryption_key"
```

Create a `.env` file (add to `.gitignore`):

```bash
cat > .env << EOF
POSTGRES_PASSWORD=your_secure_password
POSTGRES_HOST=localhost
POSTGRES_SSLMODE=require
REDIS_PASSWORD=your_redis_password
S3_BUCKET=your-bucket-name
S3_ACCESS_KEY_ID=your_access_key
S3_SECRET_ACCESS_KEY=your_secret_key
PLEXICHAT_SYSTEM_KEY=your_encryption_key
EOF

chmod 600 .env
```

---

## Security Hardening

### System Security

#### Firewall Configuration

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

#### User and Permissions

Create a dedicated user for running Plexichat:

```bash
# Create plexichat user
sudo useradd -r -s /bin/false plexichat

# Set ownership of application directory
sudo chown -R plexichat:plexichat /opt/plexichat

# Set appropriate permissions
sudo chmod 750 /opt/plexichat
```

### Application Security

#### TLS/SSL Configuration

Use a reverse proxy (nginx) for TLS termination:

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

    location /gateway {
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

#### Rate Limiting

Configure rate limiting in your configuration:

```yaml
rate_limiting:
  enabled: true
  global:
    requests: 100
    window_seconds: 60
  user:
    requests: 50
    window_seconds: 60
  ip:
    requests: 30
    window_seconds: 60
```

See [Rate Limiting Configuration](config-rate-limiting.md) for detailed rate limit settings and [Security Best Practices](security.md) for security considerations.

---

## Deployment Patterns

### Single Server Deployment

Suitable for small deployments (<1,000 users):

```bash
# Run as plexichat user
sudo -u plexichat bash

# Activate virtual environment
cd /opt/plexichat
source .venv/bin/activate

# Start server
uvicorn main:app --host 0.0.0.0 --port 8000 --workers 4
```

### Systemd Service

Create a systemd service for automatic startup:

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

Enable and start the service:

```bash
sudo systemctl daemon-reload
sudo systemctl enable plexichat
sudo systemctl start plexichat
sudo systemctl status plexichat
```

### Multi-Worker Deployment

For higher throughput, use multiple workers:

```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --workers 8
```

Calculate workers based on CPU cores: `workers = (2 * CPU cores) + 1`

### Horizontal Scaling

For large deployments (>1,000 users), deploy multiple instances behind a load balancer:

**Requirements:**

- Shared PostgreSQL database
- Shared Redis instance
- Shared storage (S3 or similar)
- Load balancer (nginx, HAProxy, or cloud LB)

**Load Balancer Configuration (nginx):**

```nginx
upstream plexichat {
    least_conn;
    server 10.0.1.10:8000;
    server 10.0.1.11:8000;
    server 10.0.1.12:8000;
}

server {
    listen 443 ssl http2;
    server_name chat.example.com;

    # SSL configuration...

    location / {
        proxy_pass http://plexichat;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location /gateway {
        proxy_pass http://plexichat;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

**Important:** Enable sticky sessions for WebSocket connections. Most load balancers support this via cookie-based routing.

---

## Verification

### Health Checks

Verify the server is operational:

```bash
# Health check
curl https://chat.example.com/health

# Version check
curl https://chat.example.com/api/v1/version

# Status check
curl https://chat.example.com/api/v1/status
```

Expected responses:

- `/health`: `{"status": "ok"}`
- `/api/v1/version`: `{"version": "a.1.0-51", "minimum_client_version": "a.1.0-0"}`
- `/api/v1/status`: Server state, version, uptime, and maintenance info

### API Documentation

Access interactive documentation:

- Swagger UI: `https://chat.example.com/docs`
- ReDoc: `https://chat.example.com/redoc`
- OpenAPI spec: `https://chat.example.com/openapi.json`

### WebSocket Connection

Test WebSocket connectivity:

```bash
wscat -c wss://chat.example.com/gateway
```

Connection should succeed and begin receiving gateway events.

### Database Connectivity

Verify database connection:

```bash
# Test PostgreSQL connection
psql -h localhost -U plexichat -d plexichat -c "SELECT 1;"

# Check migration status
psql -h localhost -U plexichat -d plexichat -c "SELECT * FROM schema_migrations;"
```

### Redis Connectivity

Verify Redis connection:

```bash
redis-cli ping
redis-cli info stats
```

---

## Monitoring

### Application Metrics

Enable Prometheus metrics:

```yaml
monitoring:
  enabled: true
  prometheus:
    enabled: true
    port: 9090
    path: "/metrics"
```

Access metrics at: `https://chat.example.com/metrics`

### Key Metrics to Monitor

- **Request Rate**: Requests per second by endpoint
- **Response Time**: P50, P95, P99 response times
- **Error Rate**: HTTP 5xx errors
- **Database Connections**: Active and idle connections
- **Redis Operations**: Cache hit/miss rates
- **WebSocket Connections**: Active gateway connections
- **CPU/Memory**: Server resource utilization

### Logging

Logs are written to `~/.plexichat/logs/`:

- `plexichat.log`: Main application log
- `error.log`: Error-level messages
- `access.log`: HTTP access log (if configured)

Configure log rotation:

```bash
# Create logrotate configuration
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
        systemctl reload plexichat
    endscript
}
EOF
```

### Alerting

Configure alerts for:

- Server down (health check fails)
- High error rate (>5%)
- High response time (P95 > 1s)
- Database connection failures
- Redis connection failures
- Disk space >80% full
- CPU usage >90% for >5 minutes

---

## Backup and Recovery

### Database Backups

For PostgreSQL, use pg_dump:

```bash
# Create backup
pg_dump -h localhost -U plexichat plexichat > backup-$(date +%Y%m%d).sql

# Compress backup
gzip backup-$(date +%Y%m%d).sql

# Restore from backup
gunzip backup-20240101.sql.gz
psql -h localhost -U plexichat plexichat < backup-20240101.sql
```

Automate with cron:

```bash
# Daily backup at 2 AM
0 2 * * * pg_dump -h localhost -U plexichat plexichat | gzip > /backups/plexichat-$(date +\%Y\%m\%d).sql.gz
```

### Media Backups

If using local storage:

```bash
# Backup media directory
rsync -av /home/plexichat/.plexichat/media/ /backups/media/

# Compress backup
tar -czf media-backup-$(date +%Y%m%d).tar.gz /home/plexichat/.plexichat/media/
```

If using S3, enable versioning and lifecycle policies for automatic backup.

### Configuration Backups

```bash
# Backup configuration
cp config/config.yaml /backups/config-$(date +%Y%m%d).yaml
```

### Recovery Procedure

1. Stop Plexichat service
2. Restore database from backup
3. Restore media directory (if using local storage)
4. Restore configuration
5. Start Plexichat service
6. Verify health check
7. Test critical functionality

---

## Maintenance

### Zero-Downtime Deployments

For zero-downtime updates:

1. Deploy new version to one instance at a time
2. Wait for health check to pass
3. Move load balancer traffic to new instance
4. Repeat for remaining instances
5. Verify overall system health

### Database Migrations

Migrations run automatically on startup. For production:

1. Test migrations on staging environment first
2. Create database backup before production deployment
3. Deploy during low-traffic period
4. Monitor migration logs
5. Verify application functionality after migration

### Log Rotation

Configure logrotate as shown in Monitoring section to prevent disk space issues.

### Security Updates

- Keep system packages updated: `sudo apt update && sudo apt upgrade -y`
- Update Python dependencies: `pip install --upgrade -r requirements.txt`
- Monitor security advisories for dependencies
- Schedule regular security audits

### Performance Tuning

Regularly review and tune:

- Database connection pool sizes
- Redis memory limits and eviction policies
- Worker count based on CPU cores
- Rate limiting thresholds
- Cache TTL values

---

## Troubleshooting

### Common Issues

#### Server Won't Start

**Symptoms:** Service fails to start or crashes immediately

**Check:**
```bash
# Check service status
sudo systemctl status plexichat

# View logs
sudo journalctl -u plexichat -n 100

# Check application logs
tail -100 ~/.plexichat/logs/plexichat.log
```

**Common Causes:**
- Database connection failure
- Redis connection failure
- Configuration syntax error
- Port already in use
- Missing dependencies

#### Database Connection Errors

**Symptoms:** "Database connection failed" errors in logs

**Check:**
```bash
# Test PostgreSQL connection
psql -h localhost -U plexichat -d plexichat

# Check PostgreSQL status
sudo systemctl status postgresql

# Check PostgreSQL logs
sudo tail -100 /var/log/postgresql/postgresql-*.log
```

**Solutions:**
- Verify PostgreSQL is running
- Check connection credentials in configuration
- Ensure PostgreSQL is listening on correct port
- Check firewall rules
- Verify SSL configuration

#### High CPU Usage

**Symptoms:** CPU utilization consistently >80%

**Check:**
```bash
# Check CPU usage
top

# Check process details
ps aux | grep uvicorn

# Check database query performance
psql -h localhost -U plexichat -d plexichat -c "SELECT * FROM pg_stat_activity;"
```

**Solutions:**
- Reduce worker count
- Enable Redis caching
- Optimize slow queries
- Add database indexes
- Scale horizontally

#### High Memory Usage

**Symptoms:** Memory utilization consistently >80%

**Check:**
```bash
# Check memory usage
free -h

# Check process memory
ps aux --sort=-%mem | head

# Check Redis memory
redis-cli info memory
```

**Solutions:**
- Reduce worker count
- Adjust connection pool sizes
- Configure Redis maxmemory
- Check for memory leaks
- Add more RAM

#### WebSocket Connection Issues

**Symptoms:** WebSocket connections fail or drop frequently

**Check:**
```bash
# Test WebSocket connection
wscat -c wss://chat.example.com/gateway

# Check reverse proxy configuration
sudo nginx -t

# Check proxy logs
sudo tail -100 /var/log/nginx/error.log
```

**Solutions:**
- Verify reverse proxy WebSocket configuration
- Increase proxy timeouts
- Check firewall rules for WebSocket
- Verify load balancer sticky sessions
- Check rate limiting configuration

### Getting Help

If issues persist:

1. Collect relevant logs
2. Document system configuration
3. Check [Configuration Documentation](configuration.md)
4. Review module-specific configuration guides
5. Check application logs for error messages
6. Verify all dependencies are running

---

## Related Documentation

- [Configuration Overview](configuration.md) - Configuration discovery and module-specific guides
- [Default Configuration Reference](default-config.md) - Complete configuration reference
- [Authentication Configuration](config-authentication.md) - Authentication and session configuration
- [Database Configuration](config-database.md) - Database setup and scaling
- [Redis Configuration](config-redis.md) - Caching and session storage
- [Media Configuration](config-media.md) - File storage and processing
- [Voice Configuration](config-voice.md) - Voice/video setup
- [Security Best Practices](security.md) - Security hardening
- [Performance Guide](performance.md) - Performance optimization
