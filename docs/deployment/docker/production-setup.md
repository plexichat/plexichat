# Production setup

Deploy Plexichat to production with security, TLS, and scaling.

## Pre-production checklist

Before you deploy, verify the following:

- [ ] You generate strong, unique values for `POSTGRES_PASSWORD`, `VALKEY_PASSWORD`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`, and `BACKUP_RESTIC_PASSWORD` with the [environment generator](/docs/api/deployment/env-generator) or `openssl rand -base64 32`.
- [ ] You store `.env` outside version control (for example, HashiCorp Vault, AWS Secrets Manager, or GitLab CI secrets).
- [ ] You obtain valid TLS certificates or plan to replace the self-signed `certs` volume.
- [ ] You configure firewall rules to expose only 80 and 443.
- [ ] You test restic backups and restores.
- [ ] You configure monitoring for `/api/v1/ready` and `/api/v1/health`.

## Production startup

Set hardened values in `plexichat/.env`, then start the stack:

```bash
docker compose up -d
```

Verify health:

```bash
docker compose ps
curl -sf http://localhost:8000/api/v1/ready
curl -sf http://localhost:8000/api/v1/health | jq .
```

### Capture the bootstrap admin password

On the first start of a fresh install the backend prints the generated
`admin` password **once** to the container console:

```bash
docker compose logs backend | grep -A 6 "FIRST BOOT"
```

The plaintext password is never written to disk or log files by default —
only the console line above and the Argon2id hash exist. If console capture
is not possible, set `PLEXICHAT_SAVE_ADMIN_CREDENTIALS=1` in `.env` before
first boot; the credentials are then written `0600` (atomically) to
`~/.plexichat/admin_credentials.txt`, and startup fails hard if that secure
write cannot be completed. Store the password, delete any file, change the
password on first login, and set up 2FA.

## Security hardening

### 1. Use strong secrets

Generate passwords:

```bash
openssl rand -base64 32
```

Update `plexichat/.env`:

```bash
POSTGRES_PASSWORD=<32-char-random-value>
VALKEY_PASSWORD=<32-char-random-value>
S3_ACCESS_KEY=<random-access-key>
S3_SECRET_KEY=<random-secret-key>
BACKUP_RESTIC_PASSWORD=<random-restic-password>
```

The Compose file enforces required secrets and fails fast if they are empty (`plexichat/docker-compose.yml:32`, `plexichat/docker-compose.yml:48`, `plexichat/docker-compose.yml:248`).

Never commit `.env` to Git. Store secrets in a secret manager.

### 2. Configure TLS and HTTPS

The `cert-init` service (`plexichat/docker-compose.yml:118`) generates a self-signed certificate in the `certs` volume on first boot. Nginx uses it at `/etc/nginx/certs/fullchain.pem` and `/etc/nginx/certs/privkey.pem` (`plexichat-client/docker/nginx/default.conf:21`).

For production, replace it with a valid certificate:

1. Obtain a certificate with Let's Encrypt:

   ```bash
   certbot certonly --standalone -d yourdomain.com
   ```

2. Copy the certificate into the `certs` volume:

   ```bash
   docker compose exec client sh -c "cat > /tmp/fullchain.pem" < /etc/letsencrypt/live/yourdomain.com/fullchain.pem
   docker compose exec client sh -c "cat > /tmp/privkey.pem" < /etc/letsencrypt/live/yourdomain.com/privkey.pem
   docker compose cp /tmp/fullchain.pem client:/etc/nginx/certs/fullchain.pem
   docker compose cp /tmp/privkey.pem client:/etc/nginx/certs/privkey.pem
   docker compose restart client
   ```

   Alternatively, mount your host certificate directory as a volume override for `client`.

3. If you use a custom Nginx configuration path, update `plexichat-client/docker/nginx/default.conf:21`.

HTTP on port 80 redirects to HTTPS (`plexichat-client/docker/nginx/default.conf:1`).

### 3. Isolate the network

All services share `plexichat_default`. Only these host ports are published by default:

- `client`: 80/TCP, 443/TCP (`plexichat/docker-compose.yml:236`)
- `backend`: 8000/TCP, 30000–30100/UDP (`plexichat/docker-compose.yml:205`)

The following services are internal-only and use `expose` instead of `ports`:

- `db:5432`, `valkey:6379`, `rustfs:9000/9001`, `clamav:3310`, `meilisearch:7700`, `elasticsearch:9200`

External traffic reaches only Nginx. Restrict direct backend access with a firewall if you keep `8000` published:

```bash
# Allow HTTP and HTTPS, block direct backend access from the public interface
iptables -A INPUT -p tcp --dport 80 -j ACCEPT
iptables -A INPUT -p tcp --dport 443 -j ACCEPT
iptables -A INPUT -p tcp --dport 8000 -j DROP
```

For internal RustFS debugging, add a temporary `ports` override as described in [Docker deployment guide](index.md#access-points) instead of publishing it permanently.

### 4. Run the backend as non-root

The backend image runs as the unprivileged user `plexichat` (UID 10001) (`plexichat/Dockerfile:34`). The image creates and owns `/home/plexichat/.plexichat` (the user's `$HOME`) so the `plexichat_home` volume inherits correct ownership. It must not live under `/root`: that directory is mode `0700` owned by root, so UID 10001 cannot traverse it and the server exits with `PermissionError` while creating its logs directory.

Verify the runtime user:

```bash
docker compose exec backend id
# uid=10001(plexichat) gid=10001(plexichat)
```

### 5. Set resource limits

Add limits in `plexichat/docker-compose.yml`. For example:

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

clamav:
  deploy:
    resources:
      limits:
        memory: 4G
```

ClamAV loads signatures into memory (~1.2 GB baseline, ~2.4 GB during reloads). Reserve at least 2 GB for it.

### 6. Protect logs

Logs may contain sensitive data. Restrict access or forward logs centrally:

```yaml
# Example log driver in docker-compose.yml
services:
  backend:
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "3"
```

Or forward with syslog:

```bash
docker compose logs -f backend | logger -t plexichat-backend
```

## Scaling considerations

### Cross-worker architecture

Plexichat supports horizontal scaling with multiple Uvicorn workers behind Nginx. Each worker runs the full FastAPI application. Valkey coordinates state across workers.

How it works:

1. **Session tracking** - Each worker registers WebSocket connections in Valkey with a unique `worker_id`. `SessionManager` uses Valkey sets (`user:{id}:connections`) and hashes (`conn:{id}:info`) so every worker has a global view of connections.
2. **Event dispatch** - When a worker emits an event (for example, a new message), `EventManager` sends it to local connections and publishes it to the Valkey pub/sub channel `plexichat:events:cross_worker`. Each other worker's cross-worker listener dispatches the event to its own local WebSocket connections.
3. **Loop avoidance** - Each published message includes the publisher's worker ID. The listener ignores its own echoes, so events are not delivered twice.
4. **Reconnection** - The listener reconnects with exponential backoff (1 s to 60 s cap) if Valkey disconnects.

Prerequisites for multi-worker scaling:

- Valkey must be enabled. Set `valkey.enabled: true` in `plexichat/config/docker-config.yaml:12` and ensure the `valkey` service is `healthy`.
- Rate limiting must use Valkey or database storage. In-memory limiters have independent counters per worker and allow bypass. The server logs a warning if it detects this.
- Snowflake IDs use `worker_id` (0–31) and `datacenter_id` to generate globally unique, time-sortable IDs.

Run multiple workers:

```bash
# Uvicorn directly
uvicorn src.api.app:create_app --host 0.0.0.0 --port 8000 --workers 4

# With Docker Compose (remove the backend ports mapping first)
docker compose up --scale backend=4 -d
```

When you scale to more than one replica, comment out the `ports` section on `backend` (`plexichat/docker-compose.yml:205`). Host ports 8000/TCP and 30000–30100/UDP can bind only once. Nginx at `:443` load-balances to `backend:8000` with `ip_hash` for sticky WebSocket sessions (`plexichat-client/docker/nginx/default.conf:12`).

Monitor worker health:

- **Admin Panel > Dashboard** - Shows the **Cross-Worker Event Dispatch** card with listener status (`connected`, `reconnecting`, `disconnected`), backoff, and poll count.
- `GET /api/v1/health` - Returns `cross_worker_listener` with the same fields plus `enabled` (`plexichat/src/api/routes/health.py:46`).
- `GET /api/v1/admin/dashboard` - Includes `worker_health`.

### Single-host deployment (recommended for Proxmox)

Use this for 1–50 concurrent users and up to 100K messages per day:

```bash
docker compose up -d
```

Resource guidance:

- **CPU**: 2–4 cores
- **RAM**: 4–8 GB (8 GB recommended when ClamAV is enabled)
- **Storage**: 50–500 GB (depends on media volume)

### Multi-host deployment (Kubernetes)

For larger scale, move to Kubernetes:

1. Use a managed PostgreSQL service (for example, AWS RDS).
2. Use a managed Valkey or Redis service (for example, AWS ElastiCache).
3. Run backend replicas across nodes.
4. Use an ingress controller for TLS and routing.
5. Provision persistent volumes for media or use S3.

### Proxmox-specific notes

For Proxmox VMs or LXC containers:

- **Recommended VM**: 2–4 vCPUs, 8 GB RAM, 100 GB SSD, 1 Gbps network.
- **Performance tuning**: Enable nested virtualization only if you run nested VMs. Use snapshot schedules for backups.
- **ARM64 support**: Images are multi-arch. Build for ARM hosts:

  ```bash
  docker buildx build --platform linux/arm64 -t plexichat:latest -f plexichat/Dockerfile plexichat
  ```

  Or let Docker auto-detect the platform:

  ```bash
  docker compose up -d
  ```

## Database configuration

### Connection pooling (production)

Tune pool settings in `plexichat/.env` (see `plexichat/.env.example:28`):

```bash
DB_POOL_MIN_CONNECTIONS=20
DB_POOL_MAX_CONNECTIONS=100
DB_POOL_CONNECT_TIMEOUT=10
DB_POOL_MAX_IDLE_TIME=300
```

Adjust based on concurrent WebSocket connections, request rate, and query complexity. See [Connection pooling](connection-pooling.md).

### Backups

The `backup` service (`plexichat/docker-compose.yml:240`) uses restic for daily, encrypted, deduplicated snapshots at 02:00 by default. It backs up the PostgreSQL dump, `plexichat_home`, and RustFS data.

Configure backups in `plexichat/.env` (see `docker/README.md:96`):

| Variable | Default | Description |
|----------|---------|-------------|
| `BACKUP_RESTIC_PASSWORD` | Required | Restic repository password |
| `BACKUP_SCHEDULE` | `0 2 * * *` | Cron schedule (supercronic) |
| `BACKUP_KEEP_DAILY` | `7` | Daily snapshots to keep |
| `BACKUP_KEEP_WEEKLY` | `4` | Weekly snapshots to keep |
| `BACKUP_KEEP_MONTHLY` | `6` | Monthly snapshots to keep |
| `BACKUP_KEEP_YEARLY` | `0` | Yearly snapshots to keep |

Common commands:

```bash
# Run a backup now
docker compose exec backup /scripts/backup.sh run

# List snapshots
docker compose exec backup /scripts/backup.sh list

# Restore the database from the latest snapshot
docker compose exec backup /scripts/backup.sh restore-db latest

# Restore all files to /tmp/restore inside the backup container
docker compose exec backup /scripts/backup.sh restore latest /tmp/restore
```

Set `BACKUP_SECONDARY_REPO` to replicate snapshots to S3, SFTP, or B2.

### Upgrade procedure

1. Stop services:

   ```bash
   docker compose stop
   ```

2. Back up the database:

   ```bash
   docker compose exec db pg_dump -U plexichat plexichat > backup.sql
   ```

3. Pull the new version:

   ```bash
   git pull origin main
   ```

4. Rebuild images:

   ```bash
   docker compose build --no-cache
   ```

5. Start with automatic migrations:

   ```bash
   docker compose up -d
   ```

6. Verify health:

   ```bash
   docker compose ps
   curl -sf http://localhost:8000/api/v1/ready
   ```

7. To roll back:

   ```bash
   docker compose stop
   cat backup.sql | docker compose exec -T db psql -U plexichat plexichat
   docker compose up -d
   ```

## Monitoring and alerting

### Built-in health

Check the readiness probe (Docker health check at `plexichat/docker-compose.yml:194` and `plexichat/Dockerfile:43`):

```bash
curl -sf http://localhost:8000/api/v1/ready
```

Check deep health (DB, Valkey, cross-worker listener):

```bash
curl -sf http://localhost:8000/api/v1/health | jq .
```

View backend logs:

```bash
docker compose logs backend --tail 100 | grep -E "ALERT|WARNING|ERROR"
```

### External monitoring

**Prometheus**: Scrape `/api/v1/health` and `/api/v1/ready`. There is no dedicated `/metrics` endpoint.

```yaml
scrape_configs:
  - job_name: 'plexichat'
    static_configs:
      - targets: ['localhost:8000']
    metrics_path: '/api/v1/health'
```

**Backup health**: The `backup` service reports liveness through `/scripts/healthcheck.sh` (`plexichat/docker-compose.yml:274`). Check `docker compose ps backup`.

## Next steps

- [Configuration](configuration.md) - Customize settings.
- [Health checks](healthchecks.md) - Configure service health.
- [Backup & recovery](../disaster-recovery.md) - Test restore procedures.
