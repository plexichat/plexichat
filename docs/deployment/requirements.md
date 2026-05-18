# System Requirements

This document outlines the system requirements for running Plexichat in different environments.

## Minimum Requirements

### Operating System
- Linux: Ubuntu 20.04+, Debian 11+, CentOS Stream 9+, or equivalent
- Windows: Server 2019+ or Windows 10+
- macOS: 10.15+ (for development/testing only)

### Hardware
- **RAM**: Minimum 2GB, Recommended 4GB+
- **CPU**: Minimum 2 cores, Recommended 4+ cores
- **Storage**: Minimum 10GB SSD, Recommended 50GB+ SSD
- **Network**: 1Gbps+ recommended

### Software Dependencies
- **Python**: **3.11+** (both server and client; 3.10 will not work due to 3.11+ language features in the server codebase)
- **Git**: 2.20+ (for cloning repositories)
- **pip**: 21.0+ (Python package manager)

## Production Requirements

### Database
- **PostgreSQL**: 12+ (required for production)
- **Connection**: TCP/IP access from application server
- **Extensions**: pg_trgm (for text search), uuid-ossp (for UUID generation)

### Cache (Strongly Recommended)
- **Redis**: 6+ (for shared state, sessions, and rate limiting)
- **Persistence**: AOF or RDB enabled for durability

### Storage
- **Primary**: Local filesystem with adequate permissions
- **Optional**: S3-compatible service (AWS S3, MinIO, Ceph, etc.) for media attachments
- **Bucket**: Dedicated bucket with proper CORS configuration if using S3

### Reverse Proxy (Recommended)
- **NGINX**: 1.18+ or **Traefik**: 2.0+
- **SSL/TLS**: Let's Encrypt or corporate certificates
- **HTTP/2**: Enabled for better performance

### Monitoring & Logging
- **Log Storage**: Adequate disk space or remote logging solution
- **Metrics**: Status endpoints available at `/health`, `/api/v1/status` for external monitoring tools (no built-in Prometheus endpoint)
- **Telemetry**: Optional telemetry module can be configured for external metrics collection
- **Health Checks**: Internal endpoints available at `/health`, `/api/v1/status`

## Development Requirements

### Database
- **SQLite**: 3.30+ (included with Python standard library)
- **No external database required** for development

### Optional Tools
- **Node.js**: 16+ (only for running client E2E tests with Playwright)
- **Docker**: 20.10+ (for containerized development)
- **Git LFS**: 2.0+ (if working with large binary files)

## Resource Consumption Estimates

### Base Installation
- **Disk Space**: ~2GB (code, dependencies, virtual environments)
- **Memory**: ~500MB RSS at idle
- **CPU**: <5% average at idle

### Under Load (per worker)
- **Memory**: Additional 100-200MB per 100 concurrent connections
- **CPU**: Scales linearly with request rate
- **Storage**: Depends on media upload volume and retention policy

### Database
- **Initial Size**: ~50MB (empty database with schema)
- **Growth**: Depends on message volume, user count, and retention settings
- **Recommended**: Monitor and plan for 20% monthly growth as baseline

### Redis (if used)
- **Memory**: 50-150MB base + ~1KB per active session
- **Persistence**: RDB snapshots every 5-15 minutes or AOF logging

## Network Requirements

### Ports
- **HTTP/HTTPS**: 80/443 (or custom via reverse proxy)
- **Application Server**: 8000 (configurable, typically behind proxy)
- **Client Server**: 5000 (configurable, typically behind proxy; served by the Flask client app)
- **PostgreSQL**: 5432 (default)
- **Redis**: 6379 (default)

### Bandwidth
- **Minimum**: 10Mbps for basic usage
- **Recommended**: 100Mbps+ for media-heavy usage
- **Peak**: Scale with concurrent users and media transfer volume

## Virtualization & Containerization

### Supported Platforms
- **Bare Metal**: Full performance, direct hardware access
- **Virtual Machines**: KVM, VMware, Hyper-V, VirtualBox
- **Containers**: Docker, containerd, CRI-O (community-supported)
- **Orchestration**: Kubernetes, Docker Swarm, Nomad

### Resource Allocation Guidelines
- **CPU**: Reserve at least 50% headroom for spikes
- **Memory**: Never overcommit; ensure swap is configured appropriately
- **Storage**: Use SSD for database and application layers
- **Network**: VirtIO or SR-IOV for best performance in VMs

## Security Considerations

### System Hardening
- **Updates**: Regular OS and package updates
- **Firewall**: Restrict access to required ports only
- **Users**: Run services under dedicated non-root accounts
- **Permissions**: Principle of least privilege for file system access

### Application Security
- **Secrets**: Never commit passwords or keys to version control
- **Encryption**: Use TPM or environment variable for system key
- **Headers**: Security headers configured via middleware
- **CORS**: Restrict origins to trusted domains

## Compatibility Notes

### Architecture
- **Primary**: x86_64 (AMD64/Intel64)
- **Secondary**: ARM64 (Apple Silicon, Raspberry Pi 4+, AWS Graviton)
- **Note**: Some dependencies may have limited ARM support

### Python Packages
All required Python packages are available on PyPI for:
- Linux (manylinux2014 wheels)
- Windows (win_amd64 wheels)
- macOS (macosx_10_9_x86_64 and universal2 wheels)

## Scaling Guidelines

### Vertical Scaling
- Increase RAM for larger connection pools and cache
- Increase CPU for higher request processing rates
- Use faster storage (NVMe SSD) for database performance

### Horizontal Scaling
- **Stateless API**: Multiple workers behind load balancer
- **Shared State**: Redis required for session sharing and rate limiting
- **Database**: PostgreSQL handles concurrent connections well
- **Media Storage**: Shared filesystem or S3-compatible service required

### Load Balancer Configuration
- **Sticky Sessions**: Required for WebSocket connections; not required for REST API
- **Health Checks**: Use `/health` endpoint
- **WebSocket Support**: Ensure proxy supports WebSocket upgrades
- **Timeouts**: Configure appropriate read/write timeouts

## Troubleshooting Resources

### Log Locations
- **Application**: `~/.plexichat/logs/` (rotated automatically)
- **System**: `/var/log/` for system services
- **Database**: PostgreSQL logs in `pg_log/` directory
- **Redis**: Redis logs in configured log file

### Diagnostic Commands
```bash
# Check service status
systemctl status plexichat-server plexichat-client

# View recent logs
journalctl -u plexichat-server -f
journalctl -u plexichat-client -f

# Test connectivity
curl -f http://localhost:8000/health
curl -f http://localhost:8000/api/v1/version
ws://localhost:8000/gateway  # WebSocket test

# Check resource usage
htop
df -h
free -h
```

For configuration details, see [Configuration Overview](../configuration.md) and [Default Configuration Reference](../default-config.md). For performance guidance, see [Performance Tuning](../performance.md).