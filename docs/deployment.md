# Deployment Guide

This guide covers deploying PlexiChat in production environments.

## System Requirements

- Python 3.10+
- Redis (optional but recommended for sessions and presence)
- PostgreSQL (recommended for production)
- 1GB RAM minimum (2GB+ recommended)
- 10GB Disk space (depending on media storage)

## Quick Start (Docker)

The easiest way to deploy is using Docker Compose:

1. Copy `docker-compose.example.yml` to `docker-compose.yml`
2. Configure environment variables in `.env`
3. Run `docker-compose up -d`

## Manual Deployment

### 1. Database Setup

PlexiChat supports PostgreSQL for production. Create a new database and user:

```sql
CREATE DATABASE plexichat;
CREATE USER plexi WITH PASSWORD 'your_secure_password';
GRANT ALL PRIVILEGES ON DATABASE plexichat TO plexi;
```

### 2. Environment Configuration

Create a production config file at `~/.plexichat/config/config.yaml`:

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

Use a process manager like Gunicorn with Uvicorn workers:

```bash
gunicorn -w 4 -k uvicorn.workers.UvicornWorker src.api.app:app --bind 0.0.0.0:8000
```

### 4. Reverse Proxy (Nginx)

It is highly recommended to use Nginx as a reverse proxy:

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

1. **Use TLS**: Always serve the API and Client over HTTPS.
2. **Environment Secrets**: Never commit secrets to version control. Use environment variables or a secure vault.
3. **Firewall**: Only expose ports 80 and 443. Keep internal services like Redis and PostgreSQL behind the firewall.
4. **Regular Backups**: Back up the `~/.plexichat/data/keyring.json` file. If lost, encrypted data is unrecoverable.

## Monitoring

PlexiChat provides a health check endpoint at `/api/v1/health`. Integrate this with your monitoring system (Prometheus, Grafana, etc.).
