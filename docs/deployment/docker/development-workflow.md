# Development workflow

Set up a local development environment with Docker.

## Start the development environment

Start all services with development defaults:

```bash
docker compose up -d
```

This starts PostgreSQL 18, Valkey 9.1.1, RustFS, ClamAV, the backend, the client, and the backup service. The backend image is `python:3.14.8-slim-trixie` and the health check pings `GET /api/v1/ready`.

Check that all services are healthy:

```bash
docker compose ps
docker compose logs -f backend
```

## Code changes

The default `docker compose up` stack runs published images from the registry. It mounts only `config/docker-config.yaml` and `docker/runtime/client-config.js`. It does not bind-mount `./src` or the client source, so code changes require a rebuild.

### Edit backend code

1.  Edit files in `src/`.
2.  Rebuild the backend image and restart it.

```bash
# Terminal 1: watch logs
docker compose logs -f backend

# Terminal 2: edit code, then rebuild
nano src/api/app.py
docker compose build backend
docker compose up -d backend
```

If you changed dependencies, rebuild without cache:

```bash
docker compose build --no-cache backend
docker compose up -d backend
```

### Edit frontend code

The `client` container serves the published Nginx build (`nginx:stable-alpine`). It does not bind-mount the client source. For frontend iteration, run the Vite dev server locally and point it at the stack:

```bash
# Terminal 1: run Vite locally
npm run dev --prefix plexichat-client

# Terminal 2: keep the stack running
docker compose up -d
```

When you are ready to test the production build, rebuild the client image:

```bash
docker compose build client
docker compose up -d client
```

## View logs

Stream logs for a specific service:

```bash
docker compose logs -f backend
docker compose logs -f db
docker compose logs -f valkey
docker compose logs -f rustfs
```

Show only the last 50 lines:

```bash
docker compose logs -n 50 backend
```

Follow all services:

```bash
docker compose logs -f
```

## Run tests

### Unit tests

The runtime image deliberately ships no test framework: `pytest` and its
plugins live in `requirements-dev.txt`. Install the dev lock into the running
container once, then run the suite:

```bash
docker compose exec backend pip install --require-hashes -r requirements-dev.txt
docker compose exec backend pytest
```

With coverage:

```bash
docker compose exec backend pytest --cov=src
```

Run a specific file:

```bash
docker compose exec backend pytest tests/test_auth.py
```

Watch mode:

```bash
docker compose exec backend pytest-watch
```

### Self-test (API verification)

Run a comprehensive connectivity check that covers authentication, messaging, servers, media, and WebSocket:

```bash
docker compose exec backend python main.py self-test
```

This exercises real endpoints. It is non-destructive: cleanup only removes rows owned by its own `selftest_%` test accounts, so it is safe to run against a live deployment.

## Database operations

### Open a psql shell

```bash
docker compose exec db psql -U plexichat -d plexichat
```

Then run SQL:

```sql
SELECT count(*) FROM auth_users;
SELECT count(*) FROM msg_messages;
```

### Run migrations

Migrations run automatically on startup. To trigger them manually:

```bash
docker compose exec backend python main.py --db-migrate
```

### Back up the database

Export to a SQL file:

```bash
docker compose exec db pg_dump -U plexichat plexichat > backup.sql
```

Create a compressed backup:

```bash
docker compose exec db pg_dump -U plexichat plexichat | gzip > backup.sql.gz
```

### Restore the database

```bash
cat backup.sql | docker compose exec -T db psql -U plexichat plexichat
```

For file and media restores, use the Restic CLI in the backup container:

```bash
docker exec backup /scripts/backup.sh list
docker exec backup /scripts/backup.sh restore-db
docker exec backup /scripts/backup.sh restore <snapshot>
```

## Debug

### Python debugger (pdb)

Add a breakpoint:

```python
import pdb; pdb.set_trace()
```

Attach to the running container:

```bash
docker compose attach backend
```

Use `n` for next, `s` for step, `c` for continue, and `p variable` to print.

### Log-based debugging

```python
import utils.logger as logger
logger.debug(f"User ID: {user_id}")
```

View the output:

```bash
docker compose logs -f backend
```

### Environment variables

Check variables inside a container:

```bash
docker compose exec backend env | grep LOG_LEVEL
docker compose exec backend env | sort
```

View the resolved Compose config:

```bash
docker compose config | grep -A 3 "depends_on"
```

## Performance profiling

### Slow query detection

Queries slower than `slow_query_threshold_ms` (1000 ms by default) are logged:

```bash
docker compose logs backend | grep -i "slow query"
docker compose exec backend grep "slow_query_threshold" /app/config/docker-config.yaml
```

### Memory usage

Check container memory:

```bash
docker stats plexichat-backend-1
```

Watch all containers:

```bash
docker stats --no-stream
```

### API response times

Set `LOG_LEVEL=DEBUG` to include timing in structured logs:

```bash
LOG_LEVEL=DEBUG docker compose up -d backend
docker compose logs -f backend | grep -i timing
```

## Restart services

Restart a specific service:

```bash
docker compose restart backend
```

Stop and start without recreating:

```bash
docker compose stop backend
docker compose start backend
```

Restart all services:

```bash
docker compose restart
```

Rebuild both images without cache:

```bash
docker compose build --no-cache
docker compose up -d
```

## Access services

### Backend API

The backend is available directly at `http://localhost:8000` in single-replica mode. In scaled mode, remove the `8000:8000` port mapping and use the client proxy.

```bash
curl http://localhost:8000/api/v1/ready
curl http://localhost:8000/api/v1/health
curl http://localhost:8000/health
```

### Valkey CLI

Valkey requires authentication. Use `REDISCLI_AUTH` so the password does not leak into the process list:

```bash
docker compose exec valkey sh -c 'REDISCLI_AUTH="$VALKEY_PASSWORD" redis-cli'
```

Common commands inside `redis-cli`:

```text
PING
KEYS plexichat:*
GET key_name
DEL key_name
INFO stats
```

### RustFS console

Open `http://localhost:9001` in a browser only if you publish the port for local debugging. By default, RustFS ports are exposed internally. Credentials are `S3_ACCESS_KEY` and `S3_SECRET_KEY`:

```bash
grep S3_ .env
```

Check health directly:

```bash
curl http://localhost:9000/health
curl http://localhost:9001/rustfs/console/health
# From inside the Docker network:
docker compose exec backend curl -f http://rustfs:9000/health
```

### PostgreSQL CLI

```bash
docker compose exec db psql -U plexichat -d plexichat
docker compose exec db pg_isready -U plexichat
```

## Debug WebSocket connections

View gateway logs:

```bash
docker compose logs -f backend | grep -i gateway
docker compose logs -f backend | grep -i websocket
```

Test the gateway manually:

```bash
websocat ws://localhost:8000/gateway
```

Install `websocat` with `cargo install websocat`.

## Environment customization

Override variables for a single session:

```bash
LOG_LEVEL=DEBUG DB_POOL_MAX_CONNECTIONS=50 docker compose up -d
```

Or edit `.env` and restart:

```bash
nano .env
docker compose restart
```

For GPU-accelerated Shieldstral or external search, enable Compose profiles:

```bash
# Local AI moderation (downloads model to ./models/shieldstral)
docker compose --profile ai up -d

# Meilisearch
docker compose --profile search-meilisearch up -d

# Elasticsearch
docker compose --profile search-elastic up -d
```

## Access the container shell

```bash
docker compose exec backend bash
```

Inside the container:

```bash
ls -la /app
cat /app/config/docker-config.yaml
python -c "import sys; print(sys.version)"
```

## Health monitoring

Check service health:

```bash
docker compose ps
docker inspect plexichat-backend-1 --format='{{json .State.Health}}' | python3 -m json.tool
```

Manual health checks:

```bash
curl -f http://localhost:8000/api/v1/ready
curl -f http://localhost:8000/health
docker compose exec db pg_isready -U plexichat -d plexichat
docker compose exec valkey sh -c 'REDISCLI_AUTH="$VALKEY_PASSWORD" redis-cli ping | grep PONG'
docker compose exec rustfs sh -c 'curl -f http://127.0.0.1:9000/health && curl -f http://127.0.0.1:9001/rustfs/console/health'
docker compose exec clamav sh -c 'echo PING | nc -w 2 localhost 3310 | grep PONG'
docker exec backup /scripts/healthcheck.sh
```

## Next steps

*   [Configuration](configuration.md) for settings
*   [Architecture](architecture.md) for service topology
*   [Healthchecks](healthchecks.md) for probe details
