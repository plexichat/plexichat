# Production Setup

Deploy Plexichat to production with security, TLS, and scaling considerations.

## Pre-Production Checklist

Before deploying, ensure:

- [ ] All encryption keys are strong and unique (use [Environment Generator](/docs/api/deployment/env-generator))
- [ ] Database password is secure and different from defaults
- [ ] Redis password is set and secure
- [ ] TLS certificates are generated or obtained
- [ ] Firewall rules are configured
- [ ] Backups are tested and working
- [ ] Monitoring and alerting are configured
- [ ] Team has access to `.env` (securely stored, never in git)

## Production Profile

Use the `prod` profile for hardened defaults:

```bash
docker compose --profile prod up -d
```

## Security Hardening

### 1. Strong Secrets

Generate secure passwords using the [Environment Generator](/docs/api/deployment/env-generator) or manually:
```bash
openssl rand -base64 32
```

Update `.env`:
```bash
POSTGRES_PASSWORD=<your-32-char-password>
REDIS_PASSWORD=<your-32-char-password>
MINIO_ROOT_PASSWORD=<your-32-char-password>
S3_SECRET_KEY=<your-32-char-key>
```

Never commit `.env` to git. Store securely:
- GitLab Secrets / GitHub Secrets (for CI/CD)
- HashiCorp Vault (for production)
- Secrets Manager (AWS Secrets Manager, Azure Key Vault)

### 2. TLS/HTTPS

Self-signed certificates are auto-generated in `nginx-certs` volume on first run.

For production, use valid certificates:

1. Obtain certificate (Let's Encrypt recommended):
```bash
# Using certbot standalone
certbot certonly --standalone -d yourdomain.com
```

2. Copy to container:
```bash
docker compose exec client bash
cp /etc/letsencrypt/live/yourdomain.com/fullchain.pem /etc/nginx/certs/
cp /etc/letsencrypt/live/yourdomain.com/privkey.pem /etc/nginx/certs/
```

3. Update Nginx config path in `docker/nginx/default.conf` if needed

### 3. Network Isolation

Services communicate only within their networks:

- **plexichat-backend** (internal) - Database, Redis, MinIO, Backend
- **plexichat-frontend** (external) - Client, Backend

External traffic only reaches:
- HTTP port 80 (redirects to HTTPS)
- HTTPS port 443 (frontend)
- Backend API on 8000 (if exposed, restrict with firewall)

Firewall rules example (Linux iptables):
```bash
# Allow only HTTPS (443)
iptables -A INPUT -p tcp --dport 443 -j ACCEPT
iptables -A INPUT -p tcp --dport 80 -j ACCEPT
iptables -A INPUT -p tcp --dport 8000 -j DROP
```

### 4. Non-Root User

Backend runs as non-root user (uid 1000). Verify:
```bash
docker compose exec backend id
# uid=1000(plexichat) gid=1000(plexichat) groups=1000(plexichat)
```

### 5. Resource Limits

Limit container resource usage in `docker-compose.yml`:

```yaml
backend:
  deploy:
    resources:
      limits:
        cpus: '2'
        memory: 4G
      reservations:
        cpus: '1'
        memory: 2G

db:
  deploy:
    resources:
      limits:
        cpus: '2'
        memory: 4G
      reservations:
        cpus: '1'
        memory: 2G
```

### 6. Log Security

Logs contain sensitive information. Protect:

```bash
# Restrict log file permissions
docker volume inspect plexichat_backend-logs
# Copy path and restrict: chmod 600 /var/lib/docker/volumes/.../logs/*.log

# Or use log drivers to send to centralized logging
```

## Scaling Considerations

### Single-Host Deployment (Recommended for Proxmox)

Plexichat is designed for single-host deployment. Use:

```bash
docker compose --profile prod up -d
```

This works well for:
- 1-50 concurrent users
- Up to 100K messages/day
- Self-hosted Proxmox VMs

Resource requirements:
- **CPU**: 2-4 cores
- **RAM**: 4-8GB
- **Storage**: 50GB-500GB (depends on media volume)

### Multi-Host Deployment (Kubernetes)

For larger scale, migrate to Kubernetes:
1. Separate database to managed service (AWS RDS, DigitalOcean Postgres)
2. Separate Redis to managed service (AWS ElastiCache)
3. Deploy backend replicas across nodes
4. Use ingress controller for TLS/routing
5. Configure persistent volumes for media

This is beyond the scope of Docker Compose. See deployment docs.

### Proxmox-Specific Notes

Running on Proxmox LXC containers:

**Recommended VM specs:**
- CPU: 2-4 cores
- RAM: 8GB
- Disk: 100GB (SSD recommended)
- Network: 1Gbps

**Performance tuning:**
- Enable nested virtualization if running nested VMs
- Use network bonding for redundancy
- Configure automatic snapshots for backups

**ARM64 Support:**
Plexichat Docker images support arm64. Build for Proxmox ARM hosts:

```bash
docker buildx build --platform linux/arm64 -t plexichat:latest .
```

Or let Docker auto-detect platform:
```bash
docker compose --profile prod up
# Automatically uses appropriate arch
```

## Database Configuration

### Connection Pooling (Production)

Tune for production load:

```bash
DB_POOL_MIN_CONNECTIONS=20
DB_POOL_MAX_CONNECTIONS=100
DB_POOL_CONNECT_TIMEOUT=10
DB_POOL_MAX_IDLE_TIME=300
```

Adjust based on:
- Concurrent WebSocket connections
- API request rate
- Query complexity

See [Connection Pooling](connection-pooling.md) for guidance.

### Backups

Automated daily backups recommended:

```bash
# Create backup directory
mkdir -p /backups/plexichat

# Backup script (save as backup.sh)
#!/bin/bash
DATE=$(date +%Y-%m-%d_%H-%M-%S)
docker compose exec -T db pg_dump -U plexichat plexichat | \
  gzip > /backups/plexichat/backup_$DATE.sql.gz

# Schedule with cron (daily at 2 AM)
0 2 * * * /path/to/backup.sh
```

Test backup restoration regularly:
```bash
# Restore from backup
zcat backup.sql.gz | docker compose exec -T db psql -U plexichat plexichat
```

### Upgrade Procedure

Safely upgrade Plexichat:

1. Stop services:
```bash
docker compose stop
```

2. Backup database:
```bash
docker compose exec db pg_dump -U plexichat plexichat > backup.sql
```

3. Pull new version:
```bash
git pull origin main
```

4. Rebuild images:
```bash
docker compose build --no-cache
```

5. Start with migrations:
```bash
docker compose --profile prod up
# Migrations run automatically
```

6. Verify health:
```bash
docker compose ps
curl http://localhost:8000/health
```

7. Rollback if needed:
```bash
docker compose stop
docker compose down -v
cat backup.sql | docker compose exec -T db psql -U plexichat plexichat
docker compose up
```

## Monitoring & Alerting

### Built-in Monitoring

Check status:
```bash
docker compose exec backend curl http://localhost:8000/status
```

View metrics:
```bash
docker compose logs backend | grep "ALERT\|WARNING"
```

### External Monitoring

Integrate with monitoring tools:

**Prometheus**: Scrape `/metrics` endpoint
```yaml
scrape_configs:
  - job_name: 'plexichat'
    static_configs:
      - targets: ['localhost:8000']
    metrics_path: '/api/v1/metrics'
```

**Datadog**: Use Docker integration
```yaml
datadog:
  enabled: true
  api_key: ${DATADOG_API_KEY}
```

**New Relic**: Install agent in container

### Log Aggregation

Send logs to centralized system:

```bash
# Using syslog
docker compose logs -f backend | logger -t plexichat-backend
```

Or configure log driver in compose:
```yaml
logging:
  driver: "json-file"
  options:
    max-size: "10m"
    max-file: "3"
```

## Updating Documentation

After deployment, update your team docs:

- [ ] Document your domain
- [ ] Record backup procedures
- [ ] Document recovery procedures
- [ ] List monitoring dashboards
- [ ] Record contact info for on-call

## Troubleshooting Production Issues

### Container crashes

Check logs:
```bash
docker compose logs backend --tail 50
```

Common causes:
- Out of memory (check `docker stats`)
- Corrupted database (restore from backup)
- Invalid environment variables (check `.env`)

### Database connection errors

Check connection pool settings:
```bash
docker compose exec backend grep "pool" config/docker-config.yaml
```

Increase if needed:
```bash
docker compose down
# Edit .env
docker compose up
```

### High memory usage

Restart affected service:
```bash
docker compose restart backend
```

Monitor:
```bash
docker stats --no-stream
```

If persistent, increase container limits.

## Next Steps

- [Configuration](configuration.md) - Customize settings
- [Monitoring](../../monitoring.md) - Set up monitoring
- [Backup & Recovery](../../disaster-recovery.md) - Backup procedures
