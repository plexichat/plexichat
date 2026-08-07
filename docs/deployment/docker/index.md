# Docker Deployment Guide

This section covers running Plexichat in Docker: local development, production deployment, scaling, and troubleshooting.

## Quick Navigation

- [Quick Start](quick-start.md) - Get Plexichat running in 5 minutes
- [Configuration](configuration.md) - Environment variables and .env file setup
- [Development Workflow](development-workflow.md) - Hot reload, testing, debugging
- [Production Setup](production-setup.md) - Security, TLS, scaling, Proxmox deployment
- [Connection Pooling](connection-pooling.md) - Database connection tuning
- [Healthchecks](healthchecks.md) - Service health monitoring
- [Troubleshooting](troubleshooting.md) - Common issues and solutions
- [Architecture](architecture.md) - System design, networks, data flows

## What is Plexichat in Docker?

Plexichat is a multi-container application stack:

- **Backend** (FastAPI) - REST API, WebSocket gateway, business logic
- **Database** (PostgreSQL) - User data, messages, servers
 - **Cache** (Valkey) - Sessions, rate limiting, WebSocket state
- **Storage** (RustFS) - Media files (avatars, attachments)
- **Frontend** (Nginx + Vite) - Web client, reverse proxy, TLS termination

All services run in isolated containers with automatic health monitoring and dependency management.

## System Requirements

- Docker 20.10+
- Docker Compose 2.0+
- 4GB RAM minimum (8GB recommended)
- 10GB free disk space
- Linux, macOS, or Windows (with Docker Desktop)

## Startup Options

### Development (Local)
```bash
docker compose up
```
All services start with fresh debug-style `.env` defaults; adjust `LOG_LEVEL`
in `.env` for verbose logging.

### Production (Self-hosted)
```bash
docker compose up -d
```
Services start in the background with the `.env` settings you configure.

### Testing (CI)
Set `NO_STRICT_CONFIG=false` and run the backend test-suite against a scratch
database; the packaged `up` stack targets Postgres/Valkey/RustFS/ClamAV and is
not needed for isolated unit tests.

> Note: the compose stack no longer uses `--profile` flags. Whether a service
> starts is controlled purely by its presence in `docker-compose.yml` and the
> standard `.env` variables, so pinned versions (see below) and LOG_LEVEL are
> set there rather than through a provider footprint flag.

## Status Checking

View service status and health:
```bash
docker compose ps
```

Expected output shows all services with `healthy` status.

Check specific service logs:
```bash
docker compose logs -f backend
docker compose logs -f db
```

## Common Commands

Start all services:
```bash
docker compose up
```

Run in background:
```bash
docker compose up -d
```

View logs:
```bash
docker compose logs -f <service>
```

Execute commands in a container:
```bash
docker compose exec backend python main.py self-test
```

Stop all services:
```bash
docker compose down
```

Remove volumes (wipes all data):
```bash
docker compose down -v
```

## Access Points

| Service | URL | Purpose |
|---------|-----|---------|
| Backend API | http://localhost:8000/api/v1 | REST endpoints |
| API Docs | http://localhost:8000/docs | Interactive OpenAPI explorer |
| WebSocket | ws://localhost:8000/gateway | Real-time messaging |
| Frontend | http://localhost | Web client |
| RustFS Console | http://localhost:9001 | Object storage management |
| Valkey | localhost:6379 | Cache (Valkey CLI) |
| PostgreSQL | localhost:5432 | Database (CLI tools) |

## First-Time Setup

1. Generate secure `.env` using the [Environment Generator](/docs/api/deployment/env-generator)
2. Save as `.env` in your Plexichat directory
3. Run: `BACKEND_VERSION=latest CLIENT_VERSION=latest docker compose up -d`
4. Wait for all services to report `healthy`
5. Access frontend at http://localhost

For versioned deployment:
```bash
BACKEND_VERSION=a.1.0-131 CLIENT_VERSION=a.1.0-117 docker compose up -d
```

See [Quick Start](quick-start.md) for detailed steps.

## Performance Notes

- First build: 2-3 minutes (downloads base images, compiles dependencies)
- Subsequent starts: 10-30 seconds (dependent on service startup times)
- Database initialization: ~5-10 seconds on first run
- Backend healthy: 15-30 seconds (migrations, module initialization)

## Security Considerations

- **Development**: Uses placeholder passwords (changeme) - NOT FOR PRODUCTION
- **Production**: Use strong secrets in `.env` file, never commit sensitive data
- **TLS**: Self-signed certificates generated automatically for HTTPS
- **Network Isolation**: All services share a single internal Docker network; only the client's 80/443 (and optional backend 8000 + RustFS 9000/9001) are published

See [Production Setup](production-setup.md#security) for hardening guidelines.

## Data Persistence

All data is stored in named Docker volumes:

- `postgres_data` - PostgreSQL database
- `valkey_data` - Valkey cache
- `rustfs_data` - Media files (RustFS)
- `plexichat_home` - Application state (config, keyrings, media)
- `certs` - TLS certificates
- `backup_repo` - restic backup repository
- `clamav_db` - ClamAV signature database

Volumes survive container restarts but are deleted with `docker compose down -v`.

## Networking

`docker-compose.yml` creates a single default bridge network. All services
communicate by service name over that network (e.g., `backend` reaches `db`
as `http://db:5432`, and the `client` nginx reaches the API as
`http://backend:8000`). Only the `client` service publishes host ports
(80, 443) by default; `backend` (8000, 30000-30100/UDP) and `rustfs`
(9000, 9001) also publish host ports in single-instance setups.

## Next Steps

- [Quick Start](quick-start.md) - Get running immediately
- [Development Workflow](development-workflow.md) - Hot reload and testing
- [Production Setup](production-setup.md) - Deploy to production

Still have questions? See [Troubleshooting](troubleshooting.md).


