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
- **Cache** (Redis) - Sessions, rate limiting, WebSocket state
- **Storage** (MinIO) - Media files (avatars, attachments)
- **Frontend** (Nginx + Vue) - Web client, reverse proxy, TLS termination

All services run in isolated containers with automatic health monitoring and dependency management.

## System Requirements

- Docker 20.10+
- Docker Compose 2.0+
- 4GB RAM minimum (8GB recommended)
- 10GB free disk space
- Linux, macOS, or Windows (with Docker Desktop)

## Profiles

Three pre-configured profiles for different use cases:

| Profile | Use Case | Services | Config |
|---------|----------|----------|--------|
| `dev` | Local development | All services | Hot reload, debug logging, bind mounts |
| `prod` | Production deployment | All services | Hardened defaults, TLS, security configs |
| `test` | CI/testing | Backend only + SQLite | Lightweight, no external deps |

## Startup Options

### Development (Local)
```bash
docker compose --profile dev up
```
All services start with debug logging and code hot-reload.

### Production (Self-hosted)
```bash
docker compose --profile prod up -d
```
Services start in background with hardened security settings.

### Testing (CI)
```bash
docker compose --profile test up
```
Lightweight setup for automated tests.

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
docker compose exec backend python main.py --self-test
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
| MinIO Console | http://localhost:9001 | Object storage management |
| Redis | localhost:6379 | Cache (CLI tools) |
| PostgreSQL | localhost:5432 | Database (CLI tools) |

## First-Time Setup

1. Clone the repository
2. Copy `.env.example` to `.env` (optional - defaults are auto-generated)
3. Run: `docker compose --profile dev up`
4. Wait for all services to report `healthy`
5. Access frontend at http://localhost

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
- **Network Isolation**: Services communicate only within their network (backend-internal, frontend-internal)

See [Production Setup](production-setup.md#security) for hardening guidelines.

## Data Persistence

All data is stored in named Docker volumes:

- `db-data` - PostgreSQL database
- `redis-data` - Redis cache
- `minio-data` - Media files
- `backend-data` - Application state
- `backend-logs` - Log files
- `backend-media` - Uploaded files
- `backend-temp` - Temporary files

Volumes survive container restarts but are deleted with `docker compose down -v`.

## Networking

Docker Compose creates two networks:

- **plexichat-backend** - Database, Redis, MinIO, Backend (internal)
- **plexichat-frontend** - Backend, Client (external HTTP/HTTPS)

Services on the same network can communicate by service name (e.g., `backend` can reach `db` as `http://db:5432`).

## Next Steps

- [Quick Start](quick-start.md) - Get running immediately
- [Development Workflow](development-workflow.md) - Hot reload and testing
- [Production Setup](production-setup.md) - Deploy to production

Still have questions? See [Troubleshooting](troubleshooting.md).
