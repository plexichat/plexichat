# Plexichat Docker Compose stack

This Compose file orchestrates the full Plexichat deployment.

## Services

| Service | Image | Purpose |
|---|---|---|
| `backend` | `gitlab.plexichat.com/plexichat/plexichat/backend` | FastAPI server (Python `3.14.8`, see `Dockerfile:1`) |
| `db` | `postgres:18-alpine` | Primary database (`docker-compose.yml:25`) |
| `valkey` | `valkey/valkey:9.1.1-alpine` | Cache, sessions, and presence (`docker-compose.yml:43`) |
| `rustfs` | `rustfs/rustfs:1.0.0-rc.3` | S3-compatible object storage |
| `rustfs-init` | `rustfs/rc:v0.1.30` | One-shot bucket creation |
| `client` | `gitlab.plexichat.com/plexichat/plexichat-client/nginx` | Nginx for Vite-built assets |
| `cert-init` | `alpine:3.24` | Self-signed TLS generation |
| `clamav` | `clamav/clamav:1.5.4` | Malware scanning daemon (TCP `3310`) |
| `clamav-unofficial-sigs` | `ghcr.io/extremeshok/clamav-unofficial-sigs` | Third-party signature updater |
| `backup` | `gitlab.plexichat.com/plexichat/plexichat/backup` | Restic snapshots of DB, `~/.plexichat`, and RustFS |
| `shieldstral` | `ghcr.io/ggml-org/llama.cpp:server-b10614` | Optional local AI moderation (profile `ai`) |
| `meilisearch` | `getmeili/meilisearch:v1.12` | Optional search backend (profile `search-meilisearch`) |
| `elasticsearch` | `docker.elastic.co/elasticsearch/elasticsearch:8.15.0` | Optional search backend (profile `search-elastic`) |

## Deploy quickly

The deploy scripts handle credential generation, config creation, and Compose download. You do not need to clone the repo.

**Linux or macOS:**

```bash
curl -sSL https://plexichat.com/deploy.sh | bash
```

**Windows (PowerShell):**

```powershell
irm https://plexichat.com/deploy.ps1 | iex
```

By default the scripts prompt you for values. Use `--non-interactive` (or `-NonInteractive` on Windows) to accept defaults.

## Run from a clone (development)

1. Create `.env`, `config/docker-config.yaml`, and `docker/runtime/client-config.js`. The deploy script generates these for you.
2. Start the stack:

   ```bash
   BACKEND_VERSION=a.1.0-134 CLIENT_VERSION=a.1.0-119 docker compose up -d
   ```

## Verify the deployment

Run these checks after startup:

```bash
curl http://127.0.0.1:8000/api/v1/ready
curl http://127.0.0.1:8000/api/v1/health
curl -k https://127.0.0.1/
curl -k https://127.0.0.1/docs
```

The backend healthcheck is `curl -sf http://localhost:8000/api/v1/ready` (`docker-compose.yml:194`). The gateway lives at `ws://127.0.0.1:8000/gateway` (`src/api/websocket/gateway.py:261`).

## Scale to multiple workers

When you run more than one backend replica, Valkey is required for cross-worker events (`plexichat:events:cross_worker`), sessions, and rate limiting.

1. Comment out the `ports` section on the `backend` service (host ports `8000/TCP` and `30000-30100/UDP` can bind only once).
2. Scale with:

   ```bash
   docker compose up --scale backend=4 -d
   ```

The `client` Nginx at `:443` load-balances to `backend:8000` via Docker DNS.

## Configure malware scanning

ClamAV scans uploads in parallel with compression. The `clamav` service loads signatures into memory (~1.2 GB baseline, ~2.4 GB during reloads) and is limited to 4 GB.

| Variable | Default | Description |
|---|---|---|
| `SCANNER_ENABLED` | `false` | Enable ClamAV scanning |
| `SCANNER_HOST` | `clamav` | ClamAV hostname |
| `SCANNER_PORT` | `3310` | ClamAV port |

The Compose file sets `SCANNER_ENABLED=true` and `SCANNER_HOST=clamav` for the backend (`docker-compose.yml:171`). Signature updates are automatic and persisted in the `clamav_db` volume.

## Configure backups

The `backup` container uses restic to create deduplicated, encrypted snapshots containing:

* `tmp/backup/db/plexichat.sql.gz` — PostgreSQL dump
* `plexichat-home/` — config, keyrings, and `.machine_key`
* `rustfs-data/` — uploaded media

### Environment variables

| Variable | Default | Description |
|---|---|---|
| `BACKUP_RESTIC_PASSWORD` | *(required)* | Restic repository password |
| `BACKUP_SCHEDULE` | `0 2 * * *` | Cron schedule |
| `BACKUP_KEEP_DAILY` | `7` | Daily snapshots to keep |
| `BACKUP_KEEP_WEEKLY` | `4` | Weekly snapshots to keep |
| `BACKUP_KEEP_MONTHLY` | `6` | Monthly snapshots to keep |
| `BACKUP_KEEP_YEARLY` | `0` | Yearly snapshots to keep |
| `BACKUP_INCLUDE_DB` | `true` | Include DB dump |
| `BACKUP_INCLUDE_HOME` | `true` | Include `/plexichat-home` |
| `BACKUP_INCLUDE_MEDIA` | `true` | Include `/rustfs-data` |
| `BACKUP_SECONDARY_REPO` | *(empty)* | Secondary repo URI (`s3:`, `sftp:`, `b2:`, etc.) |
| `BACKUP_SECONDARY_PASSWORD` | *(empty)* | Password for secondary repo |
| `BACKUP_SUCCESS_HOOK` | *(empty)* | Hook on success |
| `BACKUP_FAILURE_HOOK` | *(empty)* | Hook on failure |

### Back up and restore commands

```bash
# List snapshots
docker exec plexichat-backup-1 /scripts/backup.sh list

# Restore the database from the latest snapshot
docker exec plexichat-backup-1 /scripts/backup.sh restore-db latest

# Restore files to /tmp/restore
docker exec plexichat-backup-1 /scripts/backup.sh restore latest /tmp/restore

# Run a backup now
docker exec plexichat-backup-1 /scripts/backup.sh run

# Show statistics
docker exec plexichat-backup-1 /scripts/backup.sh stats
```

Set `BACKUP_SECONDARY_REPO` to replicate snapshots off-site.

## Configure search backends

| Profile | Backend | Memory | When to use |
|---|---|---|---|
| *(none)* | SQLite `FTS5` / Postgres `tsvector` | ~0 | Default, low-resource |
| `search-meilisearch` | Meilisearch `v1.12` | ~150 MB | Typo-tolerant search |
| `search-elastic` | Elasticsearch `8.15.0` | ~1 GB | Full-text at scale |

Start with a profile:

```bash
docker compose --profile search-meilisearch up -d
```

## Enable local AI moderation (optional)

Start Shieldstral with the `ai` profile:

```bash
docker compose --profile ai up -d
```

The deploy script downloads the model to `./models/shieldstral` and wires `automod.ai.shieldstral` for you. GPU acceleration uses `docker-compose.gpu.yml` when `nvidia-container-toolkit` is present.

## File locations

* Compose file: `docker-compose.yml`
* Docker config template: `config/docker-config.yaml`
* Backup scripts: `docker/backup/`
* Client config: `docker/runtime/client-config.js`

For full backup restore procedures, see the backup section in this file and the comments in `docker-compose.yml`.
