# Plexichat Docker Compose Stack

This compose file lives in the `plexichat` repo and orchestrates:
- backend (FastAPI server)
- db (PostgreSQL 16)
- redis (Redis 7)
- minio (S3-compatible storage + bucket init)
- client (Nginx serving Vite-built static assets)
- cert-init (self-signed TLS certificate generation)
- **backup** (restic-based automated backup — DB, files, MinIO)

## Quick Deploy

The recommended way to deploy Plexichat is via the standalone deploy scripts,
which handle credential generation, config file creation, and compose file
download without requiring a git clone:

**Linux / macOS:**
```bash
curl -sSL https://plexichat.com/deploy.sh | bash
```

**Windows (PowerShell):**
```powershell
irm https://plexichat.com/deploy.ps1 | iex
```

The scripts are interactive by default. Pass `--non-interactive` (or `-NonInteractive`)
to use defaults and skip prompts. See `--help` for all available flags.

## Manual Start (Developer Workflow)

If you have the repository cloned locally, you can start the stack directly:

```bash
# Generate .env, config/docker-config.yaml, and docker/runtime/client-config.js
# using the deploy script, or create them manually.

# Start the stack
VERSION=a.1.0-84 docker compose up -d
```

## Verify

```bash
curl http://localhost:8000/api/v1/health
curl -k https://localhost/
curl -k https://localhost/docs
```

## Backup Service

The `backup` container uses **restic** for block-level deduplicated,
encrypted backups. On each run it creates a single snapshot containing:

- `db_dump/plexichat.sql.gz` — PostgreSQL dump
- `plexichat-home/` — server config, keyrings, encryption keys
- `minio-data/` — all uploaded media and attachments

### Configuration (via `.env`)

| Variable | Default | Description |
|----------|---------|-------------|
| `BACKUP_RESTIC_PASSWORD` | *(auto-generated)* | restic repository encryption password |
| `BACKUP_SCHEDULE` | `0 2 * * *` | Cron expression for daily backups |
| `BACKUP_KEEP_DAILY` | `7` | Daily snapshots to retain |
| `BACKUP_KEEP_WEEKLY` | `4` | Weekly snapshots to retain |
| `BACKUP_KEEP_MONTHLY` | `6` | Monthly snapshots to retain |
| `BACKUP_KEEP_YEARLY` | `0` | Yearly snapshots to retain |
| `BACKUP_INCLUDE_DB` | `true` | Include PostgreSQL dump |
| `BACKUP_INCLUDE_HOME` | `true` | Include `/plexichat-home` (config + keyrings) |
| `BACKUP_INCLUDE_MINIO` | `true` | Include `/minio-data` (file uploads) |
| `BACKUP_SECONDARY_REPO` | *(empty)* | Secondary restic repo URI (e.g. `s3:...`) |
| `BACKUP_SECONDARY_PASSWORD` | *(empty)* | Password for secondary repo |
| `BACKUP_SUCCESS_HOOK` | *(empty)* | Command/URL on success |
| `BACKUP_FAILURE_HOOK` | *(empty)* | Command/URL on failure |

### CLI (via `docker exec`)

```bash
# List all snapshots
docker exec plexichat-backup-1 /scripts/backup.sh list

# Restore database from latest snapshot
docker exec plexichat-backup-1 /scripts/backup.sh restore-db latest

# Restore all files from latest snapshot to /tmp/restore
docker exec plexichat-backup-1 /scripts/backup.sh restore latest /tmp/restore

# Run backup immediately
docker exec plexichat-backup-1 /scripts/backup.sh run

# Show repository statistics
docker exec plexichat-backup-1 /scripts/backup.sh stats
```

### Off-site / Secondary Storage

Set `BACKUP_SECONDARY_REPO` to any restic-supported backend
(`s3:`, `sftp:`, `rclone:`, `b2:`, `azure:`, `gs:`, etc.).
The backup run replicates each snapshot to both the local repository
and the secondary destination, with independent retention policies.
