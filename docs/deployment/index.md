# Deployment Documentation

Welcome to the Plexichat deployment documentation. This section covers everything you need to deploy, configure, and operate a Plexichat server.

## Quick Start

New to Plexichat deployment? Start here:

- **[Getting Started](getting-started.md)** - Step-by-step production deployment guide with Docker
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

The recommended way to deploy Plexichat:

```yaml
# See getting-started.md for complete docker-compose.yml
services:
  plexichat:
    build: .
    ports:
      - "8000:8000"
  postgres:
    image: postgres:15-alpine
  redis:
    image: redis:7-alpine
```

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
docker-compose exec plexichat python -m alembic upgrade head

# Native
python -m alembic upgrade head
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
- [Configuration Overview](../configuration.md)
- [Default Configuration Reference](../default-config.md)
- [Admin Panel Guide](../admin/index.md)
