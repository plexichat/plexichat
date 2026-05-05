# Plexichat Docker Compose Stack

This compose file lives in the `plexichat` repo and orchestrates:
- backend (`python main.py`)
- db (Postgres)
- redis
- minio (+ bucket init)
- client (from sibling `../plexichat-client`)
- backup job

## Zero-config bootstrap

Run once:

```powershell
docker compose -f .\docker-compose.yml run --rm bootstrap
```

This generates:
- `./.env.generated` (random credentials/secrets)
- `./config/docker-config.yaml` (docker-specific backend config)
- `../plexichat-client/docker/runtime/client-config.js` (client runtime config)

## Start

```powershell
docker compose -f .\docker-compose.yml up -d cert-init
docker compose -f .\docker-compose.yml up -d --build
```

## Verify

```powershell
curl.exe http://localhost:8000/api/v1/health
curl.exe -k https://localhost/
curl.exe -k https://localhost/docs
```
