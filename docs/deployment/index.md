# Deployment Documentation

Welcome to the Plexichat deployment documentation. This section covers everything you need to deploy, configure, and operate a Plexichat server.

## Quick Start

New to Plexichat deployment? Start here:

- **[Environment Generator](/docs/api/deployment/env-generator)** - Generate secure .env file with cryptographically random values
- **[Docker Deployment](docker/index.md)** - Complete Docker setup guide (development, production, troubleshooting)
- **[Getting Started](getting-started.md)** - Step-by-step production deployment guide
- **[Requirements](requirements.md)** - System requirements for production and development
- **[Overview](overview.md)** - High-level deployment architecture

## Configuration Guides

Detailed configuration for each subsystem:

- **[Authentication](configuration/config-authentication.md)** - Password policies, 2FA, sessions, security
- **[Database](configuration/config-database.md)** - PostgreSQL/SQLite, connection pooling, migrations
- **[Email](configuration/config-email.md)** - SMTP settings for notifications
- **[Embeds](configuration/config-embeds.md)** - URL preview configuration
- **[Media](configuration/config-media.md)** - File uploads, S3/MinIO storage
- **[Rate Limiting](configuration/config-rate-limiting.md)** - API and gateway rate limits
- **[Redis](configuration/config-redis.md)** - Caching, session storage
- **[Search](configuration/config-search.md)** - Search backends and indexing
- **[Voice](configuration/config-voice.md)** - WebRTC, STUN/TURN servers
- **[WebSocket](configuration/config-websocket.md)** - Gateway settings, compression

## Deployment Topics

### Docker Deployment

The recommended way to deploy Plexichat is using Docker Compose:

- **[Docker Index](docker/index.md)** - Overview and system requirements
- **[Quick Start](docker/quick-start.md)** - Get running in 5 minutes
- **[Configuration](docker/configuration.md)** - Environment variables and profiles
- **[Development Workflow](docker/development-workflow.md)** - Hot reload, testing, debugging
- **[Production Setup](docker/production-setup.md)** - Security, TLS, scaling, Proxmox
- **[Connection Pooling](docker/connection-pooling.md)** - Database tuning
- **[Healthchecks](docker/healthchecks.md)** - Service monitoring
- **[Troubleshooting](docker/troubleshooting.md)** - Common issues and solutions
- **[Architecture](docker/architecture.md)** - System design and data flows

### Environment Setup

- **[Environment Generator](/docs/api/deployment/env-generator)** - Generate secure .env file online

### Versioning and Updates

- [Versioning and Updates](versioning.md) - Version scheme, update procedures, rollback strategies

### Environment Variables

Plexichat supports environment variable interpolation in configuration files:

```yaml
database:
  postgres:
    host: "${POSTGRES_HOST:-localhost}"
    password: "${POSTGRES_PASSWORD}"
```

See [Configuration Overview](../configuration.md) for details.

### Security Considerations

- Use `PLEXICHAT_SYSTEM_KEY` for encryption (bootstrap-level security)
- Enable 2FA for operator accounts
- Configure access token gating for closed deployments
- Review [Security Best Practices](../security.md)

## Maintenance

### Database Migrations

```bash
# Docker
docker compose exec backend python main.py

# Native
python main.py
```

### Health Monitoring

Available endpoints for monitoring:

- `GET /health` - Basic health check
- `GET /api/v1/status` - Detailed status including DB, Redis
- `GET /api/v1/version` - Version information

## Troubleshooting

### Common Issues

**Cannot connect to database:**
- Verify PostgreSQL is running and accessible
- Check connection string in config
- Ensure database exists

**Redis connection failures:**
- Verify Redis is running
- Check Redis password if configured
- Review connection pool settings

**WebSocket connection issues:**
- Verify port 8000 is accessible
- Check firewall rules
- Ensure gateway is enabled in config

For more help, see:
- [Docker Troubleshooting](docker/troubleshooting.md)
- [Configuration Overview](../configuration.md)
- [Default Configuration Reference](../default-config.md)
- [Admin Panel Guide](../admin/index.md)
