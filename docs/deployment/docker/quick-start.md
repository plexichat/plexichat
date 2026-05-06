# Docker Quick Start

Get Plexichat running locally in 5 minutes.

## Prerequisites

Ensure you have:
- Docker 20.10+ installed
- Docker Compose 2.0+ installed
- 4GB RAM available
- 10GB free disk space

Check your versions:
```bash
docker --version
docker compose version
```

## Step 1: Clone and Navigate

```bash
cd ~/coding-plexichat/plexichat
```

## Step 2: Start Services (Development Profile)

```bash
docker compose --profile dev up
```

This command:
- Builds Docker images for backend and frontend
- Starts all services (database, cache, storage, backend, frontend)
- Streams logs to your terminal
- Auto-generates `.env.generated` with secure keys on first run

Expected first-run time: 2-3 minutes (builds images and initializes database)

## Step 3: Wait for All Services to Be Healthy

Watch the logs. You should see:

```
backend    | INFO: Application startup complete
backend    | +==============================================================+
backend    | |                    Plexichat API Server                      |
backend    | |                      Version a.1.0-53                        |
backend    | +==============================================================+
client     | healthy
db         | healthy
redis      | healthy
minio      | healthy
```

All services should report `healthy` in `docker compose ps`:
```bash
docker compose ps
```

Expected output:
```
NAME       IMAGE           STATUS           PORTS
db         postgres:16     Up 2 min (healthy)   5432/tcp
redis      redis:7         Up 2 min (healthy)   6379/tcp
minio      minio:latest    Up 2 min (healthy)   9000/tcp, 9001/tcp
backend    plexichat:dev   Up 1 min (healthy)   0.0.0.0:8000->8000/tcp
client     nginx:latest    Up 1 min (healthy)   0.0.0.0:80->80/tcp, 0.0.0.0:443->443/tcp
```

## Step 4: Access Plexichat

Open your browser:

| Resource | URL |
|----------|-----|
| Web Client | http://localhost |
| API Docs (Swagger UI) | http://localhost:8000/docs |
| ReDoc | http://localhost:8000/redoc |
| MinIO Console | http://localhost:9001 |

## Step 5: Verify Backend Health

Check the health endpoint:
```bash
curl http://localhost:8000/health
```

Expected response:
```json
{
  "status": "healthy",
  "version": "a.1.0-53",
  "timestamp": "2024-01-15T10:30:45Z"
}
```

## Step 6: Run Self-Test

Verify all API endpoints:
```bash
docker compose exec backend python main.py --self-test
```

This runs automated checks on authentication, messaging, servers, and more.

## Next Steps

### Development Work

If you're developing:
1. Edit code in `./src` - changes auto-reload in container
2. View logs: `docker compose logs -f backend`
3. Run tests: `docker compose exec backend pytest`

See [Development Workflow](development-workflow.md) for details.

### Stop Services

To pause (keeps data):
```bash
docker compose stop
```

To resume:
```bash
docker compose start
```

To fully stop and remove containers (keeps volumes/data):
```bash
docker compose down
```

To remove everything including data:
```bash
docker compose down -v
```

## Troubleshooting

### Services won't start

Check logs:
```bash
docker compose logs backend
```

Common issues:
- Port already in use - change port mappings in `docker-compose.yml`
- Not enough disk space - free up ~10GB
- Docker daemon not running - restart Docker Desktop

### Backend logs show errors

First 30 seconds are normal (migrations running). Wait for:
```
INFO: Application startup complete
```

If errors persist after 60 seconds, see [Troubleshooting](troubleshooting.md).

### Forget a password?

`.env.generated` contains all auto-generated secrets. Check it:
```bash
cat .env.generated
```

### Need to reset everything?

Full wipe (removes all data):
```bash
docker compose down -v
docker system prune -a
docker compose --profile dev up
```

## Configuration

Default dev setup uses:
- Database: PostgreSQL (auto-initialized)
- Cache: Redis
- Storage: MinIO (local)
- API Port: 8000
- Frontend Port: 80/443

To customize, see [Configuration](configuration.md).

## Performance

- Initial build: 2-3 min
- Subsequent starts: 10-30 sec
- First request: May take 5-10 sec (backend warming up)
- All metrics viewable: `docker compose exec backend curl http://localhost:8000/status`

## Next Sections

- [Configuration](configuration.md) - Customize settings
- [Development Workflow](development-workflow.md) - Hot reload, testing
- [Troubleshooting](troubleshooting.md) - Solve common problems
- [Production Setup](production-setup.md) - Deploy to production
