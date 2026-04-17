# Deployment Guidance

This document provides operational guidance for deploying and running Plexichat in production environments. It covers the installation process, configuration requirements, verification procedures, and operational considerations without embedding environment-specific secrets or credentials.

## Installation

Plexichat is deployed by cloning the repository from GitLab and installing dependencies:

```bash
# Clone the repository with submodules
git clone --recurse-submodules https://gitlab.plexichat.com/plexichat/plexichat.git
cd plexichat

# Create and activate virtual environment
python -m venv .venv
# On Windows:
.venv\Scripts\activate
# On Linux/Mac:
source .venv/bin/activate

# Install dependencies
pip install --upgrade pip setuptools wheel
pip install -r requirements.txt
```

## Configuration

Configuration is loaded from multiple sources in this order:
1. Command line arguments (`--config`)
2. Environment variable `PLEXICHAT_CONFIG`
3. Auto-discovered files:
   - `./config/config.yaml` (project directory)
   - `~/.plexichat/config/config.yaml` (home directory)

The application uses a hierarchical configuration system where values from later sources override earlier ones. Key configuration areas include:

### Database Configuration
Plexichat supports both SQLite (for development/testing) and PostgreSQL (for production):
- `DATABASE_URL`: Full connection string (takes precedence if set)
- Individual PostgreSQL variables: `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DBNAME`, `POSTGRES_SSLMODE`
- Connection pool settings: `DB_POOL_MIN_CONNECTIONS`, `DB_POOL_MAX_CONNECTIONS`, etc.

### Security Settings
- Encryption key management (via TPM or environment variable `PLEXICHAT_SYSTEM_KEY`)
- Session security parameters
- Rate limiting configuration
- CORS and security headers

### Service Dependencies
- Redis (optional but recommended for production): `REDIS_ENABLED`, `REDIS_HOST`, `REDIS_PORT`
- Email notifications: `PLEXICHAT_SMTP_PASSWORD`, SMTP server settings
- Storage backends: S3/MinIO configuration for media attachments
- Monitoring and alerting: Various `MONITORING_*` environment variables

## Initialization Process

On startup, Plexichat performs the following sequence:
1. Loads and validates configuration
2. Initializes logging system
3. Sets up version utilities
4. Initializes core modules in dependency order:
   - Database connection and automatic migration execution
   - Redis connection (if enabled)
   - Authentication system
   - Messaging core
   - Remaining modules (servers, relationships, presence, etc.) in dependency-resolved order
5. Creates and configures the FastAPI application
6. Starts the Uvicorn server

Database migrations run automatically on startup and are transactional with rollback capability on failure.

## Verification Procedures

After deployment, verify the server is operating correctly by checking these endpoints:

### Health Checks
- `GET /health`: Returns 200 OK when the server is ready to serve traffic
- `GET /api/v1/version`: Returns current server version and minimum supported client version
- `GET /api/v1/status`: Returns server state, version, uptime, and maintenance information

### API Availability
- `GET /docs`: Interactive Swagger UI documentation
- `GET /redoc`: Alternative ReDoc documentation
- `GET /openapi.json`: Raw OpenAPI specification

### WebSocket Gateway
- Connect to `ws://<host>:<port>/gateway` for real-time event delivery
- Connection should succeed and begin receiving gateway events

### Client Compatibility
- `POST /api/v1/version/negotiate`: Check if a client version is compatible with the server

## Operational Considerations

### Persistence and Storage
- Data directory: `~/.plexichat/` by default (contains database, logs, media, temp, and config subdirectories)
- Media attachments stored in `~/.plexichat/media/` by default
- Ensure adequate disk space and backup strategy for the data directory
- For production, configure external storage (S3/MinIO) for media attachments

### Resource Requirements
- Minimum: 2GB RAM, 2 CPU cores, 10GB disk space
- Recommended for production: 4GB+ RAM, 4+ CPU cores, SSD storage
- Scale horizontally using multiple workers behind a load balancer (requires shared Redis)

### Network Configuration
- Default HTTP port: 8000 (configurable)
- WebSocket gateway shares the same port as HTTP
- TLS termination recommended at reverse proxy level (NGINX, Traefik, etc.)
- Configure appropriate timeouts for proxy connections

### Monitoring and Observability
- Built-in endpoints: `/health`, `/api/v1/status`, `/api/v1/version`
- Integrated telemetry collection (configurable)
- Structured logging to `~/.plexichat/logs/`
- Prometheus metrics available via `/metrics` endpoint when enabled

### Maintenance Procedures
- Zero-downtime deployments supported via rolling updates
- Database migrations are backward-compatible where possible
- Maintenance mode can be triggered via internal APIs
- Graceful shutdown with client notification (SIGINT/SIGTERM handling)

## Version Information

Plexichat uses a custom version format: `[stage].[major].[minor]-[build]`
- Stage: `a` (alpha), `b` (beta), `c` (candidate), `r` (release)
- Example: `a.1.0-51` (Alpha stage, major version 1, minor version 0, build 51)
- Version accessible via `/api/v1/version` endpoint
- Client compatibility checked via `/api/v1/version/negotiate`

## Supported Platforms
- Linux (Ubuntu 20.04+, Debian 11+, CentOS Stream 9+)
- Windows Server 2019+, Windows 10+
- macOS (for development/testing)
- Architectures: x86_64, ARM64

## Troubleshooting
- Check logs in `~/.plexichat/logs/` for startup and runtime issues
- Verify database connectivity and migration status
- Confirm all required environment variables are set
- Ensure ports are accessible and not blocked by firewalls
- Validate TLS certificates if using HTTPS
