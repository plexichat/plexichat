# Docker quick start

Run Plexichat locally in 5 minutes.

## Prerequisites

You need the following:

- Docker 20.10 or later
- Docker Compose 2.0 or later
- 4 GB RAM available
- 10 GB free disk space

Check your versions:

```bash
docker --version
docker compose version
```

## Step 1: Generate the environment file

Generate a secure `.env` file with the [environment generator](/docs/api/deployment/env-generator), or copy `plexichat/.env.example:1`:

```bash
umask 077
cp plexichat/.env.example plexichat/.env
chmod 600 plexichat/.env
# Edit plexichat/.env and set strong values for POSTGRES_PASSWORD, VALKEY_PASSWORD,
# S3_ACCESS_KEY, S3_SECRET_KEY, and BACKUP_RESTIC_PASSWORD.
```

The Compose file fails fast if any required password is empty (`plexichat/docker-compose.yml:32`, `plexichat/docker-compose.yml:48`). Do not commit `.env` to version control.

## Step 2: Start services

From the `plexichat/` directory, run:

```bash
BACKEND_VERSION=latest CLIENT_VERSION=latest docker compose up -d
```

To deploy a specific version:

```bash
BACKEND_VERSION=a.1.0-159 CLIENT_VERSION=a.1.0-122 docker compose up -d
```

This command does the following:

- Pulls images from `gitlab.plexichat.com` (or builds `backend` from `plexichat/Dockerfile:1` which uses `python:3.14.8-slim-trixie`).
- Starts `db`, `valkey`, `rustfs`, `clamav`, `backend`, and `client`.
- Applies the settings from `.env` and `plexichat/config/docker-config.yaml:1`.

The first run takes 2–3 minutes to download images, install dependencies, and initialize the database.

## Step 3: Wait for services to become healthy

Watch the logs:

```bash
docker compose logs -f backend
```

Look for:

```
backend    | INFO: Application startup complete
```

Check health status:

```bash
docker compose ps
```

Expected output (names and ports vary by Compose project name):

```
NAME                IMAGE                              STATUS              PORTS
plexichat-db-1      postgres:18-alpine                 Up 2 minutes (healthy)
plexichat-valkey-1  valkey/valkey:9.1.1-alpine          Up 2 minutes (healthy)
plexichat-rustfs-1  rustfs/rustfs:1.0.0-rc.3             Up 2 minutes (healthy)
plexichat-clamav-1  clamav/clamav:1.5.4                 Up 2 minutes (healthy)
plexichat-backend-1 plexichat:dev                      Up 1 minute (healthy)   0.0.0.0:8000->8000/tcp, 0.0.0.0:30000-30100->30000-30100/udp
plexichat-client-1  nginx:stable-alpine                Up 1 minute (healthy)   0.0.0.0:80->80/tcp, 0.0.0.0:443->443/tcp
```

`valkey`, `db`, `rustfs`, `clamav`, `backend`, and `client` all report `healthy`. Only `client` (80/443) and `backend` (8000/TCP, 30000–30100/UDP) publish host ports. `rustfs` (9000/9001), `valkey` (6379), and `db` (5432) are internal-only (`plexichat/docker-compose.yml:91`, `plexichat/docker-compose.yml:142`) unless you add a `ports` override.

## Step 4: Access Plexichat

| Resource | URL | Notes |
|----------|-----|-------|
| Web client | https://localhost | Self-signed cert on first run; `http://localhost` redirects to HTTPS (`plexichat-client/docker/nginx/default.conf:1`) |
| API through Nginx | https://localhost/api/v1 | Proxied to `backend:8000` |
| API directly | http://localhost:8000/api/v1 | Requires `backend` port 8000 published (default) |
| Swagger UI | https://localhost/docs | Proxied from `backend` |
| ReDoc | https://localhost/redoc | Proxied from `backend` |
| OpenAPI spec | https://localhost/openapi.json | Proxied from `backend` |
| Health (readiness) | http://localhost:8000/api/v1/ready | Used by Docker health check (`plexichat/Dockerfile:43`) |
| Health (deep) | http://localhost:8000/api/v1/health | Includes `cross_worker_listener` status |

For RustFS console (9001) or direct Valkey/Postgres access, use `docker compose exec`:

```bash
docker compose exec valkey redis-cli ping
docker compose exec db psql -U plexichat -d plexichat -c "SELECT 1;"
# To expose RustFS on the host, add a ports override; see index.md.
```

## Step 5: Verify backend health

Check the readiness probe:

```bash
curl http://localhost:8000/api/v1/ready
```

Expected response:

```json
{"status": "ready"}
```

Check the deep health endpoint:

```bash
curl http://localhost:8000/api/v1/health
```

Expected fields include `status`, `version`, and `cross_worker_listener` (`plexichat/src/api/routes/health.py:46`).

## Step 6: Run the self-test

Verify API endpoints:

```bash
docker compose exec backend python main.py self-test
```

This command runs automated checks for authentication, messaging, and server management.

## Next steps

### Develop locally

1. Edit code in `plexichat/src`. The backend container reflects changes after a restart (no automatic hot reload in the production image).
2. View logs:

   ```bash
   docker compose logs -f backend
   ```

3. Run tests:

   ```bash
   docker compose exec backend pytest
   ```

See [Development workflow](development-workflow.md) for details.

### Stop services

Pause without deleting data:

```bash
docker compose stop
```

Resume:

```bash
docker compose start
```

Stop and remove containers while keeping volumes:

```bash
docker compose down
```

Remove containers, networks, and all volumes (deletes all data):

```bash
docker compose down -v
```

## Troubleshooting

### Services do not start

Check logs:

```bash
docker compose logs backend
```

Common causes:

- A host port is already in use. Change the mapping in `plexichat/docker-compose.yml:205` or stop the conflicting process.
- Insufficient disk space. Free at least 10 GB.
- Docker daemon is not running. Restart Docker Desktop or `dockerd`.

### Backend logs show errors

Migrations run during the first 30 seconds. Wait for `Application startup complete`. If errors persist after 60 seconds, see [Troubleshooting](troubleshooting.md).

### You forget a password

Your `.env` file contains all secrets. Keep it secure and do not commit it:

```bash
grep -E "POSTGRES_PASSWORD|VALKEY_PASSWORD|S3_SECRET_KEY|BACKUP_RESTIC_PASSWORD" plexichat/.env
```

### You need a full reset

This command removes all data:

```bash
docker compose down -v
docker system prune -a
docker compose up -d
```

## Configuration

The default stack uses:

- PostgreSQL 18 (`postgres:18-alpine` at `plexichat/docker-compose.yml:25`)
- Valkey 9.1 (`valkey/valkey:9.1.1-alpine` at `plexichat/docker-compose.yml:43`)
- RustFS 1.0 (`rustfs/rustfs:1.0.0-rc.3`) for S3-compatible storage
- Backend on 8000/TCP and 30000–30100/UDP (`plexichat/Dockerfile:40`)
- Frontend on 80/443 (`plexichat-client/Dockerfile:16`)

To customize settings, see [Configuration](configuration.md).

## Performance

- Initial build: 2–3 minutes
- Subsequent starts: 10–30 seconds
- First request: 5–10 seconds while the backend warms up
- Check server state: `docker compose exec backend curl -sf http://localhost:8000/api/v1/health`

## Next sections

- [Configuration](configuration.md) - Customize settings.
- [Development workflow](development-workflow.md) - Hot reload and testing.
- [Troubleshooting](troubleshooting.md) - Fix common problems.
- [Production setup](production-setup.md) - Deploy to production.
