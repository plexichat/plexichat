# Docker Architecture

System design, networks, and data flows in Plexichat Docker deployment.

## System Overview

```
EXTERNAL
  |
  | HTTP/HTTPS (80, 443)
  v
+-------------------+
|    Nginx Client   |
|  (Reverse Proxy)  |
+-------------------+
  |
  |
  | Default Bridge Network (plexichat_default)
  |
  v
+-------------------+
| FastAPI Backend   | <--+
|   (REST + WS)     |    |
+-------------------+    |
  |                      |
  +------> +-----------+ <-- PostgreSQL Database
  |        | Database  |
  |        | Connection|
  |        | Pool      |
  +------> +-----------+
           |
           +------> +--------+
                    | Valkey |
                    | Cache  |
                    +--------+
                    |
                     +------> +---------+
                             | RustFS  |
                             | Storage |
                             +---------+
```

## Containers

### 1. Database (PostgreSQL)

**Purpose:** Store all application data

**Services:**
- SQL database engine
- Connection pooling
- Query optimization

**Data:**
- User accounts, messages, servers, media metadata
- Volume: `postgres_data` (persistent)

**Connections:**
- Backend (pooled: 5-20 development, 20-100 production)
- Network: default bridge network (single network in `docker-compose.yml`)

**Health Check:**
- `pg_isready` command
- Interval: 10 seconds
- Timeout: 5 seconds

### 2. Valkey (Cache / Session Store)

**Purpose:** High-speed caching and session storage

**Services:**
- In-memory key-value store (Redis-protocol compatible)
- Session cache (WebSocket connections)
- Rate limiting counters
- Pub/Sub for real-time events (including cross-worker dispatch)

**Data:**
- Sessions, presence status, pending events
- Volume: `valkey_data` (persistent)
- TTL: Keys expire automatically

**Connections:**
- Backend (pooled: 50)
- Network: default bridge network (single network in `docker-compose.yml`)

**Health Check:**
- `redis-cli ping` command
- Returns `PONG` when healthy

### 3. RustFS (Object Storage)

**Purpose:** S3-compatible storage for media files

**Services:**
- Media file storage (avatars, attachments)
- Deduplication and cleanup
- Access control
- REST API compatible with AWS S3

**Data:**
- Uploaded files, media metadata
- Volume: `rustfs-data` (persistent)

**Connections:**
- Backend (HTTP to port 9000)
- Network: `plexichat-backend`

**Health Check:**
- `curl /health` (S3) and `/rustfs/console/health` (Console)
- Interval: 15 seconds

### 4. RustFS-Init (One-time setup)

**Purpose:** Create S3 buckets and set anonymous access

**Services:**
- Waits for RustFS to be healthy
- Creates `plexichat-media` bucket
- Sets anonymous public read policy
- Exits when complete

**Dependencies:**
- Depends on: RustFS healthcheck
- Waits for: `service_completed_successfully`

**Exit Status:** Success

### 5. Cert-Init (One-time setup)

**Purpose:** Generate or verify TLS certificates

**Services:**
- Creates self-signed certificates on first run
- Generates `/etc/nginx/certs/fullchain.pem` and `privkey.pem`
- Uses OpenSSL
- Exits when complete

**Data:**
- Volume: `certs` (persistent)

**Exit Status:** Success

### 6. ClamAV (Malware Scanner)

**Purpose:** Scan uploaded files for malware using ClamAV

**Services:**
- `clamd` — anti-virus daemon (TCP 3310)
- `freshclam` — automatic signature updates (every 2 hours)

**Dependencies:**
- None (standalone)

**Resources:**
- RAM: 4 GB limit, 2 GB reservation (signatures spike during daily reload)
- Volume: `clamav_db` (persists signature databases)
- Additional sigs via `clamav-unofficial-sigs` sidecar (Sanesecurity, FOXHOLE, URLhaus, etc.)

**Connections:**
- Backend connects via `clamav:3310` (TCP, internal network)

### 7. Backend (FastAPI Server)

**Purpose:** Core application logic

**Services:**
- REST API (`/api/v1/*`)
- WebSocket gateway (`/gateway`)
- Health checks (`/health`)
- Documentation (`/docs`, `/redoc`)
- Admin UI (`/admin`)

**Dependencies:**
- Depends on: Database, Valkey, RustFS-Init, ClamAV

**Connections:**
- Database (pooled): `db:5432`
- Valkey: `valkey:6379`
- RustFS: `rustfs:9000`
- Networks: default bridge network (single network in `docker-compose.yml`)

**Volumes:**
- Config: `./config/docker-config.yaml` (mounted read-only)
- Data: `plexichat_home`, `postgres_data`, `valkey_data`, `rustfs_data`, `certs`, `backup_repo`

**Health Check:**
- `curl http://localhost:8000/api/v1/ready` (liveness probe)
- `curl http://localhost:8000/health` (deep health: DB, events, cross-worker listener)
- Interval: 15 seconds
- Start period: 10 seconds

**Ports:**
- API: 8000/TCP
- WebRTC: 30000-30100/UDP

### 8. Client (Nginx)

**Purpose:** Frontend web server and reverse proxy

**Services:**
- Static file serving (Vite-built web app)
- Reverse proxy to backend
- TLS termination
- HTTP -> HTTPS redirect

**Dependencies:**
- Depends on: Backend, Cert-Init

**Connections:**
- Backend: `http://backend:8000/api`
- Networks: default bridge network

**Volumes:**
- TLS certs: `certs` (read-only)
- Config: `docker/nginx/default.conf`
- Client runtime config: `client-config.js`

**Ports:**
- HTTP: 80/TCP
- HTTPS: 443/TCP

**Health Check:**
- HTTP on port 80
- Interval: 10 seconds

### 9. Backup (Restic)

**Purpose:** Automated encrypted, deduplicated backups

**Services:**
- Daily scheduled backups via supercronic (default 2 AM)
- PostgreSQL dump + file backup via restic
- Snapshot management with configurable retention
- Optional secondary/off-site repository replication

**Data:**
- Restic repository: `backup_repo` volume (persistent)

**Dependencies:**
- Depends on: Database (healthy)

**Volumes (read-only):**
- `postgres_data` — pg_dump source
- `plexichat_home` — config, keyrings, `.machine_key`
- `rustfs_data` — uploaded media

**Configuration:**
- All options via `.env` (`BACKUP_*` variables)
- See [`docker/README.md`](../../../docker/README.md) for full reference

**CLI:**
```bash
docker exec backup /scripts/backup.sh list           # list snapshots
docker exec backup /scripts/backup.sh restore-db      # restore database
docker exec backup /scripts/backup.sh restore <snap>  # restore files
```

## Networks

`docker-compose.yml` defines a single default bridge network
(`plexichat_default`). All services reach each other by service name over
this network:

- Backend reaches DB as `db:5432`
- Backend reaches Valkey as `valkey:6379`
- Backend reaches RustFS as `rustfs:9000`
- Client (Nginx) reaches Backend as `backend:8000`

Only the `client` service publishes host ports (80, 443). The `backend`
publishes 8000/TCP and 30000-30100/UDP for single-instance dev/prod but
those must be removed when scaling to multiple replicas (see
`docker-compose.yml` header comments). The `rustfs` service publishes
9000/9001 for the S3 API and web console.

External internet can only reach the published ports; Database, Valkey,
RustFS, ClamAV, and Backup are reachable only inside the Docker network.

## Volumes

| Volume | Owner | Purpose | Persistent |
|--------|-------|---------|-----------|
| `postgres_data` | PostgreSQL | Database files | Yes |
| `valkey_data` | Valkey | Cache dump | Yes |
| `rustfs_data` | RustFS | Media files | Yes |
| `plexichat_home` | Backend | Application state | Yes |
| `certs` | Nginx | TLS certificates | Yes |
| `backup_repo` | Backup | restic repository | Yes |
| `clamav_db` | ClamAV | Signature database | Yes |

**Data Loss Risk:**
- `docker compose down -v` deletes ALL volumes including the restic repository
- The restic repo is intended for local fast recovery; configure a
  `BACKUP_SECONDARY_REPO` for off-site protection

## Data Flows

### 1. REST API Request

```
Client (Browser)
  |
  | HTTPS (443)
  v
Nginx (Reverse Proxy)
  | --proxies to-->
  | HTTP (8000)
  v
Backend (FastAPI)
  |
  | Query
  v
Database (PostgreSQL)
  |
  | Result
  v
Backend (serializes to JSON)
  |
  v
Nginx (response)
  |
  v
Client (Browser)
```

### 2. WebSocket Connection

```
Client (Browser)
  |
  | WebSocket (wss://443)
  v
Nginx (passes through)
  | ws:// (8000)
  v
Backend (WebSocket Handler)
  |
  | Store in Valkey
  v
Valkey (Session state)
  |
  | Broadcast event
  v
Connected Clients
```

### 3. Media Upload

```
Client (Browser)
  |
  | POST /api/v1/media/upload
  |     (file data)
  v
Nginx (Reverse Proxy)
  |
  v
Backend (Media Handler)
  |
  | 1. Validate & encrypt
  | 2. Store in RustFS
  | 3. Save metadata in DB
  v
RustFS (Object Storage)
  |
  | File stored
  v
Backend (returns URL)
  |
  v
Client (can access via /api/v1/media/attachments/...)
```

### 4. Message Flow

```
User A (sends message)
  |
  | WebSocket message
  v
Backend (message handler)
  |
  | 1. Validate
  | 2. Encrypt (AES-GCM)
  | 3. Store in Database
  | 4. Publish to Valkey (pub/sub)
  v
Valkey Subscription
  |
  | Broadcast to connected users
  v
User B (receives via WebSocket)
User C (receives via WebSocket)
```

## Startup Sequence

1. Deploy script generates `.env`, `docker-config.yaml`, and `client-config.js`
2. Docker Compose parses configuration
3. Database, Valkey, RustFS services start in parallel
4. RustFS-Init waits for RustFS health, creates bucket, exits
5. Cert-Init generates TLS certs, exits
6. Backend waits for all dependencies to be healthy, then starts
7. Client waits for Backend to be healthy, then starts
8. All services report `healthy` in `docker compose ps`

**Timeline:** ~20-40 seconds from `docker compose up`

## Resource Usage

### Memory

- Backend: 500MB-2GB (depends on users)
- Database: 200MB-1GB
- Valkey: 100MB-500MB
- Client: 20MB
- RustFS: 100MB

**Total:** 1-4GB typical

### CPU

- Idle: Minimal (<5% per core)
- Active: 10-50% per core (depends on load)

### Disk

- Database: Grows with messages, files, activity
- RustFS: Grows with uploaded files
- Typical: 10GB-100GB+

## Security Model

### Network Isolation

- External internet can only reach the published ports (80, 443, plus 8000 and 9000/9001 when exposed)
- Database, Valkey, RustFS, ClamAV, and Backup are on the internal Docker network only
- Services communicate by hostname (DNS resolution in docker network)

### Authentication

- Frontend -> Backend: JWT token in header/cookie
- Backend -> Database: Username/password (hardcoded in config)
- Backend -> Valkey: Password (via `VALKEY_PASSWORD`, in config)
- Backend -> RustFS: Access key/secret (in config)

### Encryption

- TLS/HTTPS: All external communication encrypted
- Message encryption: E2E encryption in database (AES-GCM)
- Database: Passwords stored hashed (Argon2)

## Monitoring Points

### Health Checks

- Backend: `GET /health`
- Database: `pg_isready`
- Valkey: `redis-cli ping`
- RustFS: HTTP health endpoint

### Metrics

- Backend: `GET /metrics` (Prometheus format)
- Docker: `docker stats`
- Logs: `docker compose logs`

## Next Steps

- [Production Setup](production-setup.md) - Production deployment
- [Development Workflow](development-workflow.md) - Development setup
- [Troubleshooting](troubleshooting.md) - Debug issues
