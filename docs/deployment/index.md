# Deployment documentation

This section describes how to deploy, configure, and operate a Plexichat server.

## Quick start

If you're new to deploying Plexichat, start with these guides:

- [Environment generator](/docs/api/deployment/env-generator) — Generate a secure `.env` file with cryptographically random values.
- [Docker deployment](docker/index.md) — Complete Docker Compose setup for development and production.
- [Getting started](getting-started.md) — Step-by-step production deployment guide.
- [Requirements](requirements.md) — System requirements for production and development.
- [Overview](overview.md) — Deployment architecture and components.

## Configuration guides

Guides for each subsystem:

- [Authentication](configuration/config-authentication.md) — Password policies, 2FA, sessions, and security.
- [Database](configuration/config-database.md) — PostgreSQL and SQLite, connection pooling, and migrations.
- [Email](configuration/config-email.md) — SMTP settings for notifications.
- [Embeds](configuration/config-embeds.md) — URL preview configuration.
- [Media](configuration/config-media.md) — File uploads and S3 or RustFS storage.
- [Rate limiting](configuration/config-rate-limiting.md) — API and gateway rate limits.
- [Valkey](configuration/config-valkey.md) — Caching and session storage.
- [Search](configuration/config-search.md) — Search backends and indexing.
- [Voice](configuration/config-voice.md) — WebRTC and STUN/TURN servers.
- [WebSocket](configuration/config-websocket.md) — Gateway settings and compression.

## Deployment topics

### Docker deployment

Docker Compose is the recommended way to deploy Plexichat:

- [Docker index](docker/index.md) — Overview and system requirements.
- [Quick start](docker/quick-start.md) — Get running in 5 minutes.
- [Configuration](docker/configuration.md) — Environment variables and profiles.
- [Development workflow](docker/development-workflow.md) — Hot reload, testing, and debugging.
- [Production setup](docker/production-setup.md) — Security, TLS, scaling, and Proxmox.
- [Connection pooling](docker/connection-pooling.md) — Database tuning.
- [Health checks](docker/healthchecks.md) — Service monitoring.
- [Troubleshooting](docker/troubleshooting.md) — Common issues and solutions.
- [Architecture](docker/architecture.md) — System design and data flows.

### Environment setup

- [Environment generator](/docs/api/deployment/env-generator) — Generate a secure `.env` file online.

### Versioning and updates

- [Versioning and updates](versioning.md) — Version scheme, update procedures, and rollback strategies.

### Environment variables

Plexichat supports environment variable interpolation in configuration files:

```yaml
database:
  postgres:
    host: "${POSTGRES_HOST:-localhost}"
    password: "${POSTGRES_PASSWORD}"
```

For details, see the [Configuration overview](../configuration.md).

### Security considerations

- Use `PLEXICHAT_SYSTEM_KEY` for encryption at the bootstrap level.
- Enable 2FA for operator accounts.
- Configure access token gating for closed deployments.
- Review [Security best practices](../security.md).

## Maintenance

### Database migrations

Run pending migrations after updating code or configuration:

```bash
# Docker
docker compose exec backend python -m src.core.migrations.cli apply_migrations

# Native — preferred wrapper
python main.py migrate-db

# Native — direct CLI
python -m src.core.migrations.cli apply_migrations
```

To preview changes without applying them, add `--dry-run`:

```bash
python main.py migrate-db --dry-run
```

### Health monitoring

Plexichat exposes these endpoints under the API prefix (`/api/v1` by default). A root-level `/health` alias is also available.

| Endpoint | Purpose |
|---|---|
| `GET /api/v1/ready` | Readiness probe. Returns 200 as soon as the server is listening. No database or Valkey checks. Use for container orchestrator health checks. |
| `GET /api/v1/health` | Health check. Returns `status` and `version`, plus advisory `events_metrics` and `cross_worker_listener` fields. |
| `GET /api/v1/status` | Server status. Returns operational state, version, uptime, and maintenance announcements. |
| `GET /api/v1/version` | Version information. Returns the current server version and minimum supported client version. |

The Docker Compose `backend` service checks `GET http://localhost:8000/api/v1/ready`.

For details, see [Health checks](docker/healthchecks.md).

## Troubleshooting

### Common issues

**Cannot connect to database**

- Verify that PostgreSQL 18 is running and reachable.
- Check the connection string in your config.
- Ensure the database exists and the user has `CONNECT` permission.

**Valkey connection failures**

- Verify that Valkey 9.1.1 is running.
- Check `VALKEY_PASSWORD` if authentication is enabled.
- Review connection pool settings.

**WebSocket connection issues**

- Verify that port 8000 is accessible or proxied through the `client` Nginx container at 443.
- Check firewall rules and `gateway` configuration.
- Ensure `/gateway` upgrades are allowed through your reverse proxy.

For more help, see:

- [Docker troubleshooting](docker/troubleshooting.md)
- [Configuration overview](../configuration.md)
- [Default configuration reference](../default-config.md)
- [Admin panel guide](../admin/index.md)
