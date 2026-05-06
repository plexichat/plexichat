# Development Workflow

Local development setup with hot reload, testing, and debugging.

## Start Development Environment

```bash
docker compose --profile dev up
```

This starts all services with development defaults:
- Code hot-reload enabled (`./src` bind-mounted)
- Debug logging (LOG_LEVEL=DEBUG)
- Auto-restart on failure

## Hot Code Reload

Backend code changes auto-reload without restarting the container.

### Editing Backend Code

1. Edit any file in `./src`
2. Uvicorn detects changes and auto-reloads
3. Refresh browser to see changes

Example:
```bash
# Terminal 1: Watch logs
docker compose logs -f backend

# Terminal 2: Edit code
nano src/api/app.py

# Result: Backend auto-reloads, new code runs immediately
```

### Editing Frontend Code

Frontend is also bind-mounted for development:
```bash
# Terminal 1: Watch frontend
docker compose logs -f client

# Terminal 2: Edit code
nano ../plexichat-client/src/App.vue

# Result: Vite rebuilds and browser hot-reloads
```

## Viewing Logs

Stream logs in real-time:
```bash
docker compose logs -f backend
```

Only last 50 lines:
```bash
docker compose logs -n 50 backend
```

All services:
```bash
docker compose logs -f
```

Specific service:
```bash
docker compose logs -f redis
docker compose logs -f db
```

## Running Tests

### Unit Tests

```bash
docker compose exec backend pytest
```

With coverage:
```bash
docker compose exec backend pytest --cov=src
```

Specific test file:
```bash
docker compose exec backend pytest tests/test_auth.py
```

Watch mode (re-run on file changes):
```bash
docker compose exec backend pytest-watch
```

### Self-Test (API Verification)

Comprehensive API and connectivity check:
```bash
docker compose exec backend python main.py --self-test
```

Tests authentication, messaging, servers, media, WebSocket, and more.

## Database Operations

### View Database

Connect directly with psql:
```bash
docker compose exec db psql -U plexichat -d plexichat
```

Then run SQL:
```sql
SELECT COUNT(*) FROM auth_users;
SELECT COUNT(*) FROM msg_messages;
```

### Run Migrations

Automatic on startup, but manually trigger:
```bash
docker compose exec backend python main.py --db-migrate
```

### Backup Database

Export to SQL file:
```bash
docker compose exec db pg_dump -U plexichat plexichat > backup.sql
```

Compressed backup:
```bash
docker compose exec db pg_dump -U plexichat plexichat | gzip > backup.sql.gz
```

### Restore Database

```bash
cat backup.sql | docker compose exec -T db psql -U plexichat plexichat
```

## Debugging

### Python Debugger (pdb)

Insert breakpoint in code:
```python
import pdb; pdb.set_trace()
```

Then attach to running container:
```bash
docker compose attach backend
```

Type `n` (next), `s` (step), `c` (continue), `p variable` (print)

### Print Statements

Fast debugging with logs:
```python
import utils.logger as logger
logger.debug(f"User ID: {user_id}")
```

View in logs:
```bash
docker compose logs -f backend
```

### Environment Variables

Check what's set in a container:
```bash
docker compose exec backend env | grep LOG_LEVEL
```

View all:
```bash
docker compose exec backend env | sort
```

## Performance Profiling

### Slow Query Detection

Queries slower than 1000ms are logged:
```bash
docker compose logs backend | grep "slow query"
```

Check threshold in config:
```bash
docker compose exec backend grep "slow_query_threshold" config/docker-config.yaml
```

### Memory Usage

Check container memory:
```bash
docker stats plexichat-backend-1
```

Watch real-time:
```bash
docker stats --no-stream
```

### API Response Times

Enable timing headers in backend logs:
```bash
LOG_LEVEL=DEBUG docker compose up backend
```

Look for timing in structured logs.

## Manual Service Restart

Restart a specific service:
```bash
docker compose restart backend
```

Restart without stopping:
```bash
docker compose stop backend && docker compose start backend
```

Restart all:
```bash
docker compose restart
```

## Clean Build

Force rebuild image (after dependency changes):
```bash
docker compose build --no-cache backend
docker compose up backend
```

Both backend and frontend:
```bash
docker compose build --no-cache
docker compose up
```

## Accessing Services

### Backend API

```bash
curl http://localhost:8000/api/v1
```

### Redis CLI

```bash
docker compose exec redis redis-cli
```

Then use Redis commands:
```
PING                    # Test connection
KEYS *                  # List all keys
GET key_name            # Get value
DEL key_name            # Delete key
```

### MinIO Console

Open browser: http://localhost:9001

Login with credentials from `.env.generated`:
```bash
grep MINIO .env.generated
```

### PostgreSQL CLI

```bash
docker compose exec db psql -U plexichat -d plexichat
```

## Debugging WebSocket Connections

View WebSocket gateway logs:
```bash
docker compose logs -f backend | grep gateway
docker compose logs -f backend | grep websocket
```

Test WebSocket manually:
```bash
websocat ws://localhost:8000/gateway
```

(Install websocat: `cargo install websocat`)

## Environment Customization

Override variables per-session:
```bash
LOG_LEVEL=DEBUG DB_POOL_MAX_CONNECTIONS=50 docker compose up
```

Or update `.env` and restart:
```bash
# Edit .env
nano .env

# Restart services
docker compose restart
```

## Accessing Container Shell

Execute shell in running container:
```bash
docker compose exec backend bash
```

Then explore:
```bash
ls -la /app
cat config/docker-config.yaml
python -c "import sys; print(sys.version)"
```

## Health Monitoring

Check all service health:
```bash
docker compose ps
```

View detailed health:
```bash
docker inspect plexichat-backend-1 | grep -A 5 "Health"
```

Manual health check:
```bash
curl http://localhost:8000/health
docker exec plexichat-db-1 pg_isready -U plexichat
docker exec plexichat-redis-1 redis-cli ping
```

## Next Steps

- [Configuration](configuration.md) - Custom settings
- [Production Setup](production-setup.md) - Deploy to production
- [Troubleshooting](troubleshooting.md) - Solve problems
