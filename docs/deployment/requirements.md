# System requirements

This document lists the system requirements for running Plexichat in different environments.

## Minimum requirements

### Operating system

- Linux: Ubuntu 20.04+, Debian 11+, CentOS Stream 9+, or equivalent
- Windows: Server 2019+ or Windows 10+
- macOS: 10.15+ for development and testing only

### Hardware

- **RAM**: Minimum 5 GB, recommended 8 GB. The full Docker Compose stack with server, PostgreSQL 18, Valkey 9.1.1, RustFS, Nginx, and ClamAV typically uses about 5 GB at idle.
- **CPU**: Minimum 2 cores, recommended 4+ cores.
- **Storage**: Minimum 10 GB SSD, recommended 50+ GB SSD.
- **Network**: 1 Gbps+ recommended.

### Local AI moderation

Plexichat's AI moderation and assistant features run entirely on your hardware. Content never leaves your server. Each AI feature adds RAM on top of the base requirements:

| Feature | Extra RAM (minimum / recommended) | Total (minimum / recommended) |
|---|---|---|
| Base stack (no AI) | — | 5 GB / 8 GB |
| + Shieldstral local AI moderation | +3 GB / +3 GB | 8 GB / 11 GB |
| + Gemma assistant (E2B or E4B) | +3 GB / +5 GB | 8 GB / 13 GB |

These numbers include model weights and the inference runtime. The deploy script enables Shieldstral with `--ai-automod` or the interactive prompt and auto-detects a CUDA GPU to offload inference when `nvidia-container-toolkit` is available.

### Software dependencies

- **Python**: 3.11+ for the server. Python 3.10 is not supported because the codebase uses 3.11+ language features.
- **Git**: 2.20+ for cloning repositories.
- **pip**: 21.0+

## Production requirements

### Database

- **PostgreSQL**: 18. The Docker Compose file uses `postgres:18-alpine` (`plexichat/docker-compose.yml:25`). Earlier PostgreSQL versions may work but are not tested.
- **Connection**: TCP/IP access from the application server.
- **Extensions**: `pg_trgm` for text search and `uuid-ossp` for UUID generation.

### Cache (strongly recommended)

- **Valkey**: 7+. The Docker Compose file uses `valkey/valkey:9.1.1-alpine` (`plexichat/docker-compose.yml:43`).
- **Persistence**: Enable AOF or RDB for durability.

### Storage

- **Primary**: Local filesystem with adequate permissions.
- **Optional**: S3-compatible service such as AWS S3, RustFS (`rustfs/rustfs:1.0.0-rc.3` in Docker), or Ceph for media attachments.
- **Bucket**: Dedicated bucket with proper CORS configuration if you use S3.

### Reverse proxy (recommended)

- **Nginx**: 1.18+ or **Traefik**: 2.0+
- **SSL/TLS**: Let's Encrypt or corporate certificates.
- **HTTP/2**: Enabled for better performance.

### Monitoring and logging

- **Log storage**: Adequate disk space or a remote logging solution.
- **Metrics**: Status endpoints at `/api/v1/health`, `/api/v1/ready`, `/api/v1/status`, and `/api/v1/version` for external monitoring. There is no built-in Prometheus endpoint.
- **Telemetry**: Optional telemetry module for external metrics collection.
- **Health checks**: Use `GET /api/v1/ready` for readiness probes and `GET /api/v1/health` for detailed health. A root-level `GET /health` alias is also available.

## Development requirements

### Database

- **SQLite**: 3.30+ included with the Python standard library.
- No external database required for development.

### Optional tools

- **Node.js**: 20+ only for running client end-to-end tests with Playwright.
- **Docker**: 20.10+ for containerized development (Docker Compose is the primary deployment method).
- **Git LFS**: 2.0+ if you work with large binary files.

## Resource consumption estimates

### Base installation

- **Disk space**: About 2 GB for code, dependencies, and virtual environments.
- **Memory**: About 500 MB RSS at idle per worker.
- **CPU**: Less than 5% average at idle.

### Under load (per worker)

- **Memory**: Additional 100–200 MB per 100 concurrent connections.
- **CPU**: Scales linearly with request rate.
- **Storage**: Depends on media upload volume and retention policy.

### Database

- **Initial size**: About 50 MB with an empty schema.
- **Growth**: Depends on message volume, user count, and retention settings.
- **Recommendation**: Monitor and plan for 20% monthly growth as a baseline.

### Valkey (if used)

- **Memory**: 50–150 MB base plus about 1 KB per active session.
- **Persistence**: RDB snapshots every 5–15 minutes or AOF logging.

## Network requirements

### Ports

- **HTTP/HTTPS**: 80/443, or custom ports through a reverse proxy.
- **Application server**: 8000, typically behind a proxy. The `client` Nginx container listens on 80/443 and proxies `/api/v1` and `/gateway` to `backend:8000`.
- **PostgreSQL**: 5432 (default).
- **Valkey**: 6379 (default).
- **WebRTC SFU**: UDP 30000–30100 for media.

### Bandwidth

- **Minimum**: 10 Mbps for basic usage.
- **Recommended**: 100 Mbps+ for media-heavy usage.
- **Peak**: Scale with concurrent users and media transfer volume.

## Virtualization and containerization

### Supported platforms

- **Bare metal**: Full performance and direct hardware access.
- **Virtual machines**: KVM, VMware, Hyper-V, or VirtualBox.
- **Containers**: Docker Compose (primary deployment method).
- **Orchestration**: Kubernetes, Docker Swarm, or Nomad.

### Resource allocation guidelines

- **CPU**: Reserve at least 50% headroom for spikes.
- **Memory**: Never overcommit. Configure swap appropriately.
- **Storage**: Use SSD for database and application layers.
- **Network**: VirtIO or SR-IOV for best performance in VMs.

## Security considerations

### System hardening

- **Updates**: Apply OS and package updates regularly.
- **Firewall**: Restrict access to required ports only.
- **Users**: Run services under dedicated non-root accounts.
- **Permissions**: Follow the principle of least privilege for filesystem access.

### Application security

- **Secrets**: Never commit passwords or keys to version control.
- **Encryption**: Use `PLEXICHAT_SYSTEM_KEY` or a TPM for the system key.
- **Headers**: Security headers are configured through middleware.
- **CORS**: Restrict origins to trusted domains.

## Compatibility

### Architecture

- **Primary**: x86_64 (AMD64/Intel64).
- **Secondary**: ARM64 (Apple Silicon, Raspberry Pi 4+, AWS Graviton). Some dependencies may have limited ARM support.

### Python packages

All required Python packages are available on PyPI for:

- Linux (`manylinux2014` wheels)
- Windows (`win_amd64` wheels)
- macOS (`macosx_10_9_x86_64` and `universal2` wheels)

## Scaling guidelines

### Vertical scaling

- Increase RAM for larger connection pools and cache.
- Increase CPU for higher request processing rates.
- Use faster storage such as NVMe SSD for database performance.

### Horizontal scaling

- **Stateless API**: Run multiple workers behind a load balancer.
- **Shared state**: Valkey is required for session sharing and rate limiting. The cross-worker event dispatch layer uses Valkey pub/sub on `plexichat:events:cross_worker`.
- **Database**: PostgreSQL handles concurrent connections well.
- **Media storage**: Requires a shared filesystem or S3-compatible service.

### Load balancer configuration

- **Sticky sessions**: Required for WebSocket connections, not required for the REST API.
- **Health checks**: Use `GET /api/v1/ready` for readiness and `GET /api/v1/health` for liveness.
- **WebSocket support**: Ensure the proxy supports WebSocket upgrades.
- **Timeouts**: Configure appropriate read and write timeouts.

## Troubleshooting resources

### Log locations

- **Application**: `~/.plexichat/logs/`, rotated automatically.
- **System**: `/var/log/` for system services.
- **Database**: PostgreSQL logs in the `pg_log/` directory.
- **Valkey**: Valkey logs in the configured log file.

### Diagnostic commands

```bash
# Check service status
docker compose ps

# View recent logs
docker compose logs -f backend

# Test connectivity (canonical endpoints)
curl -f http://localhost:8000/api/v1/ready
curl -f http://localhost:8000/api/v1/health
curl -f http://localhost:8000/api/v1/version
curl -f http://localhost:8000/api/v1/status

# WebSocket gateway (use wscat or similar)
wscat -c ws://localhost:8000/gateway

# Check resource usage
htop
df -h
free -h
```

For configuration details, see the [Configuration overview](../configuration.md) and [Default configuration reference](../default-config.md). For performance guidance, see [Performance tuning](../performance.md).
