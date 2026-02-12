# Deployment Guide

Production deployment guide for PlexiChat.

## System Requirements

- Python 3.11+
- Redis (recommended for sessions and presence)
- PostgreSQL (recommended for production)
- 1GB RAM minimum (2GB+ recommended)
- 10GB Disk space (depending on media storage)

## Quick Start (Docker)

1. Copy `docker-compose.example.yml` to `docker-compose.yml`
2. Configure environment variables in `.env`
3. Run `docker-compose up -d`

## Manual Deployment

### 1. Database Setup

Create PostgreSQL database and user:

```sql
CREATE DATABASE plexichat;
CREATE USER plexi WITH PASSWORD 'your_secure_password';
GRANT ALL PRIVILEGES ON DATABASE plexichat TO plexi;
```

### 2. Environment Configuration

Create production config at `~/.plexichat/config/config.yaml`:

```yaml
server:
  host: 0.0.0.0
  port: 8000
  workers: 4

database:
  type: postgres
  postgres:
    host: localhost
    port: 5432
    user: plexi
    password: your_secure_password
    dbname: plexichat

redis:
  enabled: true
  host: localhost
  port: 6379

authentication:
  security:
    token_binding: true
```

### 3. Process Management

Use Gunicorn with Uvicorn workers:

```bash
gunicorn -w 4 -k uvicorn.workers.UvicornWorker src.api.app:app --bind 0.0.0.0:8000
```

### 4. Reverse Proxy (Nginx)

```nginx
server {
    listen 80;
    server_name api.yourdomain.com;

    location / {
        proxy_pass http://localhost:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

## Security Best Practices

1. **Use TLS**: Always serve API and Client over HTTPS
2. **Environment Secrets**: Never commit secrets to version control
3. **Firewall**: Only expose ports 80 and 443
4. **Regular Backups**: Back up `~/.plexichat/data/system_keyring.json` - encrypted data is unrecoverable without it

## Monitoring

Health check endpoint: `/api/v1/health`

## Documentation Endpoint

When enabled in config, API docs are served at:
- Interactive docs: `/docs`
- Alternative docs: `/redoc`
- Static docs: `/docs/api` (if configured)

## API Base URL Configuration

The API base URL is dynamically determined based on your deployment:

| Environment | Base URL |
|-------------|----------|
| Production | `https://plexichat-app.tail79f345.ts.net/api/v1` |
| Development | `http://localhost:8000/api/v1` |

All API endpoints are relative to this base URL. For example, `GET /api/v1/users/@me` becomes `https://plexichat-app.tail79f345.ts.net/api/v1/users/@me` in production.

## Database Deployment

See [Database Deployment Guide](database-deployment.md) for detailed database migration procedures.

## Database Monitoring

See [Database Monitoring Guide](database-monitoring.md) for connection pool monitoring and health checks.
