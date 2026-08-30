# Docker architecture

System design, networks, and data flows for the Plexichat Docker deployment.

## System overview

```text
EXTERNAL
  |
  | HTTPS (443, 80 redirects to 443)
  v
+-------------------+
| Client (Nginx)    |
| Reverse proxy     |
+-------------------+
  |
  | Docker default bridge network (plexichat_default)
  v
+-------------------+
| Backend (FastAPI) | <--+
| REST + WebSocket  |    |
+-------------------+    |
  |                      |
  +------> +-----------+ <-- PostgreSQL database
  |        | PostgreSQL|
  |        | 18-alpine |
  +------> +-----------+
           |
           +------> +--------+
                    | Valkey |
                    |9.1.1   |
                    |alpine  |
                    +--------+
                    |
                    +------> +---------+
                             | RustFS  |
                             | 1.0.0-  |
                             | rc.3    |
                             +---------+
```

`docker-compose.yml` defines a single default bridge network (`plexichat_default`).
All services resolve each other by service name. No custom networks are defined.

## Containers

### 1. Database (PostgreSQL)

Stores all application data.

*   **Image:** `postgres:18-alpine`
*   **Data:** User accounts, messages, servers, and media metadata.
*   **Volume:** `postgres_data` (persistent) mounted at `/var/lib/postgresql/data`.
*   **Environment:** `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` (required). Compose fails fast if `POSTGRES_PASSWORD` is unset.
*   **Network:** Default bridge network. Backend connects at `db:5432`.
*   **Connection pooling:** 5 to 20 connections for development (the `.env.example` defaults), 20 to 100+ for production. See [Connection pooling](connection-pooling.md).
*   **Health check:** `pg_isready -U ${POSTGRES_USER} -d ${POSTGRES_DB}` every 10 seconds, timeout 5 seconds, 10 retries.

### 2. Valkey (cache and session store)

High-speed caching, session storage, and cross-worker event dispatch.

*   **Image:** `valkey/valkey:9.1.1-alpine`
*   **Data:** Sessions, presence status, pending events, and rate-limit counters. Volume `valkey_data` at `/data` with `--appendonly yes`. Keys expire by TTL.
*   **Authentication:** Started with `--requirepass ${VALKEY_PASSWORD}`. Compose fails fast if `VALKEY_PASSWORD` is unset. Health check authenticates with `REDISCLI_AUTH`.
*   **Network:** Default bridge network. Backend connects at `valkey:6379` with a 50-connection pool by default (`VALKEY_CONNECTION_POOL`).
*   **Pub/Sub:** Channel `plexichat:events:cross_worker` forwards WebSocket events between backend replicas.
*   **Health check:** `REDISCLI_AUTH="..." redis-cli ping | grep PONG` every 10 seconds, timeout 5 seconds, 10 retries.

### 3. RustFS (S3-compatible object storage)

Stores media files.

*   **Image:** `rustfs/rustfs:1.0.0-rc.3`
*   **Services:** S3 API on port 9000 and web console on port 9001.
*   **Data:** Uploaded files and thumbnails. Volume `rustfs_data` at `/data`.
*   **Configuration:** `RUSTFS_ADDRESS` (default `0.0.0.0:9000`), `RUSTFS_CONSOLE_ADDRESS` (`0.0.0.0:9001`), `RUSTFS_ACCESS_KEY` / `RUSTFS_SECRET_KEY` (mapped from `S3_ACCESS_KEY` / `S3_SECRET_KEY`, both required), `RUSTFS_VOLUMES` (`/data/rustfs`), and `RUSTFS_CORS_ALLOWED_ORIGINS` (empty by default, keep private).
*   **Network:** Default bridge network. Backend connects at `rustfs:9000`. Ports 9000 and 9001 are exposed internally, not published to the host.
*   **File ownership:** RustFS runs as UID/GID `10001:10001`. The `rustfs-perms` init container (`alpine:3.24`) runs `chown -R 10001:10001 /data` before RustFS starts.
*   **Health check:** `curl -f http://127.0.0.1:9000/health && curl -f http://127.0.0.1:9001/rustfs/console/health` every 15 seconds, timeout 10 seconds, 10 retries, start period 40 seconds.

### 4. RustFS-Init (one-time setup)

Creates the S3 bucket.

*   **Image:** `rustfs/rc:v0.1.30`
*   **Behavior:** Waits for RustFS to be healthy, runs `rc alias set` and `rc mb local/${S3_BUCKET}`, then exits. Uses `restart: "no"`.
*   **Dependency:** `rustfs` with `condition: service_healthy`.
*   **Bucket:** `${S3_BUCKET}` (default `plexichat-media`).

### 5. Cert-Init (one-time setup)

Generates TLS certificates for development.

*   **Image:** `alpine:3.24`
*   **Behavior:** Installs `openssl` and creates self-signed `/certs/fullchain.pem` and `/certs/privkey.pem` if they do not exist. Exits after completion. Uses `restart: "no"`.
*   **Volume:** `certs` mounted at `/certs` inside this container and at `/etc/nginx/certs` inside the client container.
*   **Dependency:** None. The client waits for this container with `condition: service_completed_successfully` to avoid a race on first boot.

### 6. ClamAV (malware scanner)

Scans uploaded files for malware.

*   **Image:** `clamav/clamav:1.5.4`
*   **Services:** `clamd` on TCP 3310 and `freshclam` for signature updates. Limits: `StreamMaxLength`, `MaxScanSize`, and `MaxFileSize` are 600 MB, `MaxScanTime` is 300,000 ms.
*   **Volume:** `clamav_db` at `/var/lib/clamav` (persists signatures).
*   **Sidecar:** `clamav-unofficial-sigs` (`ghcr.io/extremeshok/clamav-unofficial-sigs:latest`, mode `updater`, every 2 hours) shares `clamav_db`.
*   **Network:** Default bridge network. Backend connects at `clamav:3310` (`SCANNER_HOST` / `SCANNER_PORT`).
*   **Health check:** `echo PING | nc -w 2 localhost 3310 | grep PONG` every 30 seconds, timeout 10 seconds, 5 retries, start period 120 seconds.
*   **Dependencies:** None.

### 7. Backend (FastAPI server)

Core application logic.

*   **Image:** `${CI_REGISTRY_IMAGE:-gitlab.plexichat.com/plexichat/plexichat}/backend:${BACKEND_VERSION:-latest}` built from `plexichat/Dockerfile` (`python:3.14.6-slim-trixie`, non-root user `plexichat` UID 10001).
*   **Services:** REST API at `/api/v1/*`, WebSocket gateway at `/gateway`, liveness probe at `GET /api/v1/ready`, deep health at `GET /api/v1/health` and `GET /health`, documentation at `/docs` and `/redoc`, admin UI at `/admin`.
*   **Environment:** `PLEXICHAT_CONFIG=/app/config/docker-config.yaml`, `PLEXICHAT_PUBLIC_SERVER_URL`, `NO_STRICT_CONFIG=false`, `SCANNER_ENABLED=true`, `SCANNER_HOST=clamav`, `SCANNER_PORT=3310`, `SEARCH_BACKEND=sqlite_fts5` (default).
*   **Volumes:** `./config/docker-config.yaml:/app/config/docker-config.yaml:ro` and `plexichat_home:/root/.plexichat`.
*   **Network:** Default bridge network.
    *   Database at `db:5432`
    *   Valkey at `valkey:6379`
    *   RustFS at `rustfs:9000`
    *   ClamAV at `clamav:3310`
*   **Dependencies:** `db` (`service_healthy`), `valkey` (`service_healthy`), `rustfs-init` (`service_completed_successfully`), `clamav` (`service_healthy`).
*   **Ports (single-replica mode):** `8000:8000` (TCP) and `30000-30100:30000-30100/udp` for WebRTC. When you scale with `docker compose up --scale backend=N`, remove both `ports` mappings and rely on the client Nginx proxy, which load-balances to `backend:8000` with `ip_hash` sticky sessions.
*   **Health check:** `curl -sf http://localhost:8000/api/v1/ready` every 15 seconds, timeout 5 seconds, 3 retries, start period 10 seconds. This is a pure liveness probe that returns `{"status": "ready"}` as soon as the HTTP server is bound. The same probe is defined in the Dockerfile `HEALTHCHECK`. Use `GET /health` or `GET /api/v1/health` for the deeper check that includes EventManager metrics and cross-worker listener status.

### 8. Client (Nginx)

Frontend and reverse proxy.

*   **Image:** `${CI_REGISTRY_IMAGE:-gitlab.plexichat.com/plexichat/plexichat-client}/nginx:${CLIENT_VERSION:-latest}` built from `plexichat-client/Dockerfile` (`nginx:stable-alpine` with `curl`, Vite build staged from `node:20-alpine`).
*   **Services:** Serves the Vite-built static app, reverse-proxies `/api/`, `/docs`, `/openapi.json`, `/redoc`, `/admin`, and `/gateway` to the backend upstream (`backend:8000` with `ip_hash`), terminates TLS, and redirects HTTP to HTTPS.
*   **Upstream behavior:** `client_max_body_size 10m` matches the backend body limit so uploads are not rejected at the proxy.
*   **Dependencies:** `backend` (`service_started`) and `cert-init` (`service_completed_successfully`).
*   **Volumes:** `certs:/etc/nginx/certs` and `./docker/runtime/client-config.js:/usr/share/nginx/html/config.js:ro`.
*   **Ports:** `80:80` and `443:443`.
*   **Health check:** No Docker health check is defined for this service. Verify it with `curl http://localhost/` or `curl -k https://localhost/`.
*   **Network:** Default bridge network. Reaches the backend as `http://backend:8000`.

### 9. Backup (Restic)

Automated encrypted and deduplicated backups.

*   **Image:** `${CI_REGISTRY_IMAGE:-gitlab.plexichat.com/plexichat/plexichat}/backup:${BACKEND_VERSION:-latest}` built from `plexichat/docker/backup/Dockerfile` (`alpine:3.24` with `postgresql-client`, `restic`, `supercronic 0.2.33`, `bash`, `tzdata`, `curl`, `gzip`).
*   **Schedule:** Supercronic runs `BACKUP_SCHEDULE` (default `0 2 * * *`, 2 AM) and applies retention (`BACKUP_KEEP_DAILY=7`, `BACKUP_KEEP_WEEKLY=4`, `BACKUP_KEEP_MONTHLY=6`, `BACKUP_KEEP_YEARLY=0`).
*   **Repository:** `backup_repo` at `/repo` plus optional `BACKUP_SECONDARY_REPO` for off-site replication. Requires `BACKUP_RESTIC_PASSWORD` (compose fails fast if unset).
*   **Source volumes (read-only):** `postgres_data:/var/lib/postgresql/data`, `plexichat_home:/plexichat-home`, `rustfs_data:/rustfs-data`.
*   **Dependencies:** `db` (`service_healthy`).
*   **Health check:** `/scripts/healthcheck.sh` every 5 minutes, timeout 10 seconds, 3 retries, start period 2 minutes. The script verifies supercronic is running and a recent successful backup exists.
*   **CLI:**
    ```bash
    docker exec backup /scripts/backup.sh list
    docker exec backup /scripts/backup.sh restore-db
    docker exec backup /scripts/backup.sh restore <snapshot>
    ```

### 10. Optional profile-gated services

These services start only with `--profile`.

*   **Shieldstral (AI moderation):** Profile `ai`. Image `ghcr.io/ggml-org/llama.cpp:server-b10614`, model at `/models/shieldstral/Shieldstral-1.0-3B-Q4_K_M.gguf` (configurable via `SHIELDSTRAL_MODEL`, `SHIELDSTRAL_MMPROJ`, `SHIELDSTRAL_CONTEXT`, `SHIELDSTRAL_GPU_LAYERS`). Health check `curl -sf http://localhost:8080/health`.
*   **Meilisearch:** Profiles `search` and `search-meilisearch`. Image `getmeili/meilisearch:v1.12` at `meilisearch:7700` (`MEILI_MASTER_KEY` from `MEILISEARCH_API_KEY`). Health check `wget`/`curl` on `/health`.
*   **Elasticsearch:** Profile `search-elastic`. Image `docker.elastic.co/elasticsearch/elasticsearch:8.15.0` at `elasticsearch:9200` (`ES_JAVA_OPTS`, single-node, security disabled). Health check `curl -sf http://localhost:9200/_cluster/health`.

## Networks

`docker-compose.yml` defines a single default bridge network. All services communicate by service name:

*   Backend reaches the database at `db:5432`
*   Backend reaches Valkey at `valkey:6379`
*   Backend reaches RustFS at `rustfs:9000`
*   Backend reaches ClamAV at `clamav:3310`
*   Client reaches the backend at `backend:8000`

Only `client` (80, 443) and, in single-replica mode, `backend` (8000/TCP and 30000-30100/UDP) publish host ports. `rustfs` ports 9000 and 9001 are exposed internally, not published. When you scale the backend, remove the backend `ports` section and rely on the client proxy.

## Volumes

| Volume | Used by | Purpose | Persistent |
| --- | --- | --- | --- |
| `postgres_data` | PostgreSQL, Backup (ro) | Database files | Yes |
| `valkey_data` | Valkey | Cache dump and AOF | Yes |
| `rustfs_data` | RustFS, Backup (ro), rustfs-perms | Media objects | Yes |
| `plexichat_home` | Backend, Backup (ro) | Application state at `/root/.plexichat` | Yes |
| `certs` | Cert-Init, Client | TLS certificates | Yes |
| `backup_repo` | Backup | Restic repository at `/repo` | Yes |
| `clamav_db` | ClamAV, clamav-unofficial-sigs | ClamAV signatures | Yes |
| `meilisearch_data` | Meilisearch (profile) | Search index | Yes |
| `elasticsearch_data` | Elasticsearch (profile) | Search index | Yes |

**Data loss risk:** `docker compose down -v` deletes all volumes, including the Restic repository. The repository is for fast local recovery. Configure `BACKUP_SECONDARY_REPO` for off-site protection.

## Data flows

### 1. REST API request

```text
Browser --HTTPS (443)--> Nginx --HTTP (backend:8000)--> Backend --SQL--> PostgreSQL
                                           |
                                           v
                                    Backend (JSON) --> Nginx --> Browser
```

### 2. WebSocket connection

```text
Browser --wss (443)--> Nginx --ws (backend:8000)--> Backend --store--> Valkey (session)
                                                  --pub/sub--> Valkey --> Other backends --> Clients
```

When the backend is scaled, Valkey pub/sub on `plexichat:events:cross_worker` ensures every replica receives each event.

### 3. Media upload

```text
Browser --POST /api/v1/media/upload--> Nginx --> Backend
                                           |
                                           +--> ClamAV (scan at clamav:3310)
                                           +--> Validate, encrypt, store in RustFS (rustfs:9000)
                                           +--> Save metadata in PostgreSQL
                                           v
                                    Backend returns /api/v1/media/attachments/... URL
```

### 4. Message flow

```text
User A --WebSocket--> Backend --validate--> encrypt (AES-GCM) --> store in PostgreSQL
                                  |
                                  +--> publish to Valkey pub/sub --> User B, User C via WebSocket
```

## Startup sequence

1.  The deploy script generates `.env`, `config/docker-config.yaml`, and `docker/runtime/client-config.js`.
2.  Docker Compose parses the configuration.
3.  `db`, `valkey`, `rustfs`, `clamav`, and `rustfs-perms` start in parallel.
4.  `rustfs-perms` fixes volume ownership and exits.
5.  `rustfs-init` waits for `rustfs` to be healthy, creates the bucket, and exits.
6.  `cert-init` generates certificates if missing and exits.
7.  The backend waits for `db` and `valkey` to be healthy, `rustfs-init` to complete, and `clamav` to be healthy, then starts.
8.  The client waits for `cert-init` to complete and the backend to start, then starts.
9.  `backup` waits for `db` to be healthy, then starts supercronic.

Typical startup time is 20 to 40 seconds from `docker compose up`.

## Resource usage

### Memory

*   Backend: 500 MB to 2 GB depending on users and `BACKEND_REPLICAS`
*   Database: 200 MB to 1 GB
*   Valkey: 100 MB to 500 MB
*   RustFS: ~100 MB
*   ClamAV: ~1 to 2 GB after signatures load
*   Client (Nginx): ~20 MB
*   Meilisearch (if enabled): ~150 to 512 MB
*   Elasticsearch (if enabled): ~1 to 2 GB

Typical total without search is 1 to 4 GB.

### CPU

*   Idle: Less than 5% per core
*   Active: 10% to 50% per core depending on load

### Disk

*   Database grows with messages and activity
*   RustFS grows with uploaded files
*   Typical total: 10 GB to 100 GB or more

## Security model

### Network isolation

External clients reach only published ports (80, 443, and optionally 8000 and 30000-30100/UDP in single-replica mode). Database, Valkey, RustFS, ClamAV, backup, and search services are reachable only inside the Docker network.

### Authentication

*   Frontend to backend: JWT in header or cookie
*   Backend to PostgreSQL: `POSTGRES_USER` and `POSTGRES_PASSWORD` from `.env`
*   Backend to Valkey: `VALKEY_PASSWORD` from `.env`
*   Backend to RustFS: `S3_ACCESS_KEY` and `S3_SECRET_KEY` from `.env`
*   Backend to Meilisearch: `MEILISEARCH_API_KEY` if configured

### Encryption

*   External traffic uses TLS/HTTPS (certificates in the `certs` volume)
*   Messages are encrypted at rest in the database (AES-GCM)
*   Passwords are hashed with Argon2

## Monitoring points

*   **Liveness:** `GET /api/v1/ready` returns `{"status": "ready"}`. Used by the Docker health check for both the Dockerfile and `docker-compose.yml`.
*   **Deep health:** `GET /api/v1/health` and `GET /health` return `status`, `version`, `events_metrics`, `is_queue_recreate_pending`, and `cross_worker_listener`.
*   **Database:** `pg_isready`
*   **Valkey:** `redis-cli ping` with `REDISCLI_AUTH`
*   **RustFS:** `GET /health` and `GET /rustfs/console/health`
*   **ClamAV:** `PING` over TCP 3310
*   **Backup:** `/scripts/healthcheck.sh`

## Next steps

*   [Production setup](production-setup.md) for hardening
*   [Development workflow](development-workflow.md) for local iteration
*   [Troubleshooting](troubleshooting.md) for common issues
