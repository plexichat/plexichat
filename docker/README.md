# Plexichat Docker Compose Stack

This compose file lives in the `plexichat` repo and orchestrates:
- backend (FastAPI server)
- db (PostgreSQL 16)
- redis (Redis 7)
- minio (S3-compatible storage + bucket init)
- client (Nginx serving Vite-built static assets)
- cert-init (self-signed TLS certificate generation)
- backup job

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
VERSION=a.1.0-59 docker compose up -d
```

## Verify

```bash
curl http://localhost:8000/api/v1/health
curl -k https://localhost/
curl -k https://localhost/docs
```
