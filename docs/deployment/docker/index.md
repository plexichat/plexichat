# Docker deployment guide

This section describes how to run Plexichat with Docker and Docker Compose.

## Quick navigation

- [Quick start](quick-start.md) - Run Plexichat locally in 5 minutes.
- [Configuration](configuration.md) - Configure environment variables and the `.env` file.
- [Development workflow](development-workflow.md) - Use hot reload, tests, and debugging.
- [Production setup](production-setup.md) - Harden, configure TLS, and scale the deployment.
- [Connection pooling](connection-pooling.md) - Tune database connection pools.
- [Health checks](healthchecks.md) - Monitor service health.
- [Troubleshooting](troubleshooting.md) - Diagnose and fix common issues.
- [Architecture](architecture.md) - Understand networks, services, and data flows.

## What is Plexichat in Docker?

Plexichat runs as a multi-container stack:

- **Backend** (FastAPI, Python 3.14) - REST API, WebSocket gateway, and business logic.
- **Database** (PostgreSQL 18) - Stores users, messages, and servers.
- **Cache** (Valkey 9.1) - Stores sessions, rate-limit state, and WebSocket presence.
- **Storage** (RustFS 1.0) - Stores media files such as avatars and attachments.
- **Frontend** (Nginx + Vite) - Serves the web client and terminates TLS.
- **Scanner** (ClamAV 1.5) - Scans uploads for malware on TCP 3310 (internal).
- **Backup** (restic) - Creates encrypted, deduplicated snapshots.

All services share a single Docker bridge network (`plexichat_default`) and include health checks and dependency conditions.

## System requirements

- Docker 20.10 or later
- Docker Compose 2.0 or later
- 4 GB RAM minimum, 8 GB recommended
- 10 GB free disk space
- Linux, macOS, or Windows with Docker Desktop

## Start the stack

Start in the foreground for development:

```bash
docker compose up
```

Start in the background for self-hosted production:

```bash
docker compose up -d
```

Run backend tests against an isolated database without the full stack:

```bash
NO_STRICT_CONFIG=false python -m pytest
```

The Compose file (`plexichat/docker-compose.yml:1`) does not use `--profile` flags for core services. A service starts when it is present in `docker-compose.yml` and its `.env` variables are set. Optional services (search, AI moderation) use profiles; see `docker/README.md`.

## Check service health

List service status and health:

```bash
docker compose ps
```

All services report `healthy` when ready. Check logs for a specific service:

```bash
docker compose logs -f backend
docker compose logs -f db
```

## Common commands

| Action | Command |
|--------|---------|
| Start all services | `docker compose up` |
| Start in background | `docker compose up -d` |
| View logs | `docker compose logs -f <service>` |
| Run a command in the backend | `docker compose exec backend python main.py self-test` |
| Stop all services | `docker compose down` |
| Remove volumes and delete all data | `docker compose down -v` |

## Access points

Only the following ports are published to the host by default (`plexichat/docker-compose.yml:205`, `plexichat/docker-compose.yml:236`):

| Service | Host port | Container port | URL | Purpose |
|---------|-----------|----------------|-----|---------|
| Frontend (Nginx) | 80 | 80 | http://localhost | Web client (redirects to HTTPS) |
| Frontend (Nginx) | 443 | 443 | https://localhost | Web client with TLS |
| Backend API | 8000 | 8000 | http://localhost:8000 | REST API and docs |
| Backend voice | 30000–30100/UDP | 30000–30100/UDP | — | Voice media (SFU) |
| PostgreSQL | — | 5432 | Internal `db:5432` | Database; access with `docker compose exec db psql` |
| Valkey | — | 6379 | Internal `valkey:6379` | Cache; access with `docker compose exec valkey redis-cli` |
| RustFS S3 API | — | 9000 | Internal `rustfs:9000` | S3 API; not published by default |
| RustFS console | — | 9001 | Internal `rustfs:9001` | Console; publish with a `ports` override if needed |
| ClamAV | — | 3310 | Internal `clamav:3310` | Scanner daemon |

To expose RustFS on the host for direct debugging, add a temporary override:

```yaml
# docker-compose.override.yml
services:
  rustfs:
    ports:
      - "9000:9000"
      - "9001:9001"
```

Direct backend access:

- REST: `http://localhost:8000/api/v1`
- Swagger UI: `http://localhost:8000/docs` (or `https://localhost/docs` through Nginx)
- ReDoc: `http://localhost:8000/redoc`
- OpenAPI spec: `http://localhost:8000/openapi.json`
- Readiness probe: `http://localhost:8000/api/v1/ready`
- Deep health: `http://localhost:8000/api/v1/health`
- WebSocket gateway: `ws://localhost:8000/gateway` (through Nginx: `wss://localhost/gateway`)

## First-time setup

1. Generate a secure `.env` file with the [environment generator](/docs/api/deployment/env-generator) and save it as `.env` in `plexichat/`. See `.env.example:1` for all variables.
2. Start the stack:

   ```bash
   BACKEND_VERSION=latest CLIENT_VERSION=latest docker compose up -d
   ```

   To deploy a pinned version:

   ```bash
   BACKEND_VERSION=a.1.0-131 CLIENT_VERSION=a.1.0-117 docker compose up -d
   ```

3. Wait until `docker compose ps` shows `healthy` for `db`, `valkey`, `rustfs`, `clamav`, `backend`, and `client`.
4. Open `https://localhost` (accept the self-signed certificate on first run) or `http://localhost:8000/docs` for the API.

For detailed steps, see [Quick start](quick-start.md).

## Startup times

- First build: 2–3 minutes (downloads base images and installs Python 3.14 dependencies from `plexichat/Dockerfile:1`).
- Subsequent starts: 10–30 seconds.
- Database initialization: 5–10 seconds on first run.
- Backend `healthy` (migrations and module initialization): 15–30 seconds.

## Security considerations

- Development `.env` examples use placeholder passwords. Do not use them in production.
- Production requires strong, unique values for `POSTGRES_PASSWORD`, `VALKEY_PASSWORD`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`, and `BACKUP_RESTIC_PASSWORD` (`plexichat/.env.example:17`, `plexichat/docker-compose.yml:32`).
- Self-signed certificates are generated in the `certs` volume on first boot (`plexichat/docker-compose.yml:118`). Replace them with Let's Encrypt certificates for production.
- All services communicate over the internal `plexichat_default` network. Only `client` (80/443) and `backend` (8000/TCP, 30000–30100/UDP) publish host ports by default.

See [Production setup](production-setup.md#security) for hardening steps.

## Data persistence

All data lives in named Docker volumes:

- `postgres_data` - PostgreSQL data
- `valkey_data` - Valkey append-only file
- `rustfs_data` - Media objects
- `plexichat_home` - Application state at `/root/.plexichat` (config, keyrings, logs)
- `certs` - TLS certificates
- `backup_repo` - Restic repository
- `clamav_db` - ClamAV signature database
- `meilisearch_data`, `elasticsearch_data` - Search indexes (when enabled)

Volumes persist across container restarts. `docker compose down -v` deletes them.

## Networking

`plexichat/docker-compose.yml` creates a single default bridge network. Services reach each other by service name, for example `backend` reaches `db` at `db:5432` and `valkey` at `valkey:6379`. The `client` Nginx reaches the API at `http://backend:8000` (`plexichat-client/docker/nginx/default.conf:53`) and load-balances WebSocket connections with `ip_hash` (`plexichat-client/docker/nginx/default.conf:12`).

When you scale the backend (`docker compose up --scale backend=4`), remove the `ports` mapping on `backend` (`plexichat/docker-compose.yml:205`) and rely on Nginx at `:443`.

## Next steps

- [Quick start](quick-start.md) - Run the stack now.
- [Development workflow](development-workflow.md) - Develop with hot reload.
- [Production setup](production-setup.md) - Deploy to production.

If you encounter issues, see [Troubleshooting](troubleshooting.md).
