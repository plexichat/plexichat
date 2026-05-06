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
  | Internal Network (plexichat-frontend)
  |
  v
+-------------------+
| FastAPI Backend   | <--+
|   (REST + WS)     |    |
+-------------------+    |
  |                      |
  | Internal Network (plexichat-backend)
  |
  +------> +-----------+ <-- PostgreSQL Database
  |        | Database  |
  |        | Connection|
  |        | Pool      |
  +------> +-----------+
           |
           +------> +--------+
                    | Redis  |
                    | Cache  |
                    +--------+
                    |
                    +------> +---------+
                             | MinIO   |
                             | Storage |
                             +---------+
```

## Containers

### 1. Bootstrap (One-time initialization)

**Purpose:** Generate configuration files on first startup

**Services:**
- Runs once at startup
- Creates `.env.generated` with secure keys
- Creates `config/docker-config.yaml` from template
- Exits when complete

**Exit Status:** Success
- Other services start only after bootstrap completes

**Data:**
- Output: `.env.generated`, `config/docker-config.yaml`
- Input: None (uses defaults)

### 2. Database (PostgreSQL)

**Purpose:** Store all application data

**Services:**
- SQL database engine
- Connection pooling
- Query optimization
- Backup capability

**Data:**
- User accounts, messages, servers, media metadata
- Volume: `db-data` (persistent)

**Connections:**
- Backend (pooled: 5-20 development, 20-100 production)
- Network: `plexichat-backend`

**Health Check:**
- `pg_isready` command
- Interval: 10 seconds
- Timeout: 5 seconds

### 3. Redis (Cache)

**Purpose:** High-speed caching and session storage

**Services:**
- In-memory key-value store
- Session cache (WebSocket connections)
- Rate limiting counters
- Pub/Sub for real-time events

**Data:**
- Sessions, presence status, pending events
- Volume: `redis-data` (persistent)
- TTL: Keys expire automatically

**Connections:**
- Backend (pooled: 50)
- Network: `plexichat-backend`

**Health Check:**
- `redis-cli ping` command
- Returns `PONG` when healthy

### 4. MinIO (Object Storage)

**Purpose:** S3-compatible storage for media files

**Services:**
- Media file storage (avatars, attachments)
- Deduplication and cleanup
- Access control
- REST API compatible with AWS S3

**Data:**
- Uploaded files, media metadata
- Volume: `minio-data` (persistent)

**Connections:**
- Backend (HTTP to port 9000)
- Network: `plexichat-backend`

**Health Check:**
- HTTP health endpoint
- Interval: 10 seconds

### 5. MinIO-Init (One-time setup)

**Purpose:** Create S3 buckets

**Services:**
- Waits for MinIO to be ready
- Creates `plexichat-media` bucket
- Sets bucket policies
- Exits when complete

**Dependencies:**
- Depends on: MinIO healthcheck
- Waits for: `service_completed_successfully`

**Exit Status:** Success

### 6. Cert-Init (One-time setup)

**Purpose:** Generate or verify TLS certificates

**Services:**
- Creates self-signed certificates on first run
- Generates `/etc/nginx/certs/fullchain.pem` and `privkey.pem`
- Uses OpenSSL
- Exits when complete

**Data:**
- Volume: `nginx-certs` (persistent)

**Exit Status:** Success

### 7. Backend (FastAPI Server)

**Purpose:** Core application logic

**Services:**
- REST API (`/api/v1/*`)
- WebSocket gateway (`/gateway`)
- Health checks (`/health`)
- Documentation (`/docs`, `/redoc`)
- Admin UI (`/admin`)

**Dependencies:**
- Depends on: Bootstrap, Database, Redis, MinIO-Init, Cert-Init

**Connections:**
- Database (pooled): `db:5432`
- Redis: `redis:6379`
- MinIO: `minio:9000`
- Networks: `plexichat-backend`, `plexichat-frontend`

**Volumes:**
- Config: `./config/docker-config.yaml` (mounted)
- Source code: `./src` (mounted for hot reload)
- Data: `backend-data`, `backend-logs`, `backend-media`, `backend-temp`

**Health Check:**
- `curl http://localhost:8000/health`
- Interval: 10 seconds
- Start period: 30 seconds

**Ports:**
- API: 8000/TCP
- WebRTC: 30000-30100/UDP

### 8. Client (Nginx)

**Purpose:** Frontend web server and reverse proxy

**Services:**
- Static file serving (Vue.js app)
- Reverse proxy to backend
- TLS termination
- HTTP -> HTTPS redirect

**Dependencies:**
- Depends on: Backend, Cert-Init

**Connections:**
- Backend: `http://backend:8000/api`
- Networks: `plexichat-frontend`

**Volumes:**
- TLS certs: `nginx-certs` (read-only)
- Config: `docker/nginx/default.conf`
- Client runtime config: `client-runtime`

**Ports:**
- HTTP: 80/TCP
- HTTPS: 443/TCP

**Health Check:**
- HTTP on port 80
- Interval: 10 seconds

## Networks

### plexichat-backend (Internal)

**Services:**
- Database
- Redis
- MinIO
- Backend
- Bootstrap
- MinIO-Init
- Cert-Init

**Purpose:** Isolated internal network for data tier

**Communication:**
- All services on this network reach each other by hostname
- Example: Backend reaches DB as `http://db:5432`
- External access: None (no published ports except backend:8000)

**Security:** Services can't be reached from outside

### plexichat-frontend (External)

**Services:**
- Backend
- Client

**Purpose:** Frontend communication network

**Communication:**
- Client reaches Backend as `http://backend:8000`
- External users reach Client on port 80/443

**Security:**
- HTTP/HTTPS exposed to internet (ports 80, 443)
- Backend API on 8000 (internal only)

## Volumes

| Volume | Owner | Purpose | Persistent |
|--------|-------|---------|-----------|
| `db-data` | PostgreSQL | Database files | Yes |
| `redis-data` | Redis | Cache dump | Yes |
| `minio-data` | MinIO | Media files | Yes |
| `backend-data` | Backend | Application state | Yes |
| `backend-logs` | Backend | Log files | Yes |
| `backend-media` | Backend | Uploaded media cache | Yes |
| `backend-temp` | Backend | Temporary files | Yes |
| `nginx-certs` | Nginx | TLS certificates | Yes |
| `client-runtime` | Bootstrap | Client JS config | Yes |

**Data Loss Risk:**
- `docker compose down -v` deletes ALL volumes
- Keep regular backups of `db-data` and `minio-data`

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
  | Store in Redis
  v
Redis (Session state)
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
  | 2. Store in MinIO
  | 3. Save metadata in DB
  v
MinIO (Object Storage)
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
  | 4. Publish to Redis (pub/sub)
  v
Redis Subscription
  |
  | Broadcast to connected users
  v
User B (receives via WebSocket)
User C (receives via WebSocket)
```

## Startup Sequence

1. Docker Compose parses configuration
2. Bootstrap service starts, generates config, exits
3. Database, Redis, MinIO services start in parallel
4. MinIO-Init waits for MinIO health, creates bucket, exits
5. Cert-Init generates TLS certs, exits
6. Backend waits for all dependencies to be healthy, then starts
7. Client waits for Backend to be healthy, then starts
8. All services report `healthy` in `docker compose ps`

**Timeline:** ~20-40 seconds from `docker compose up`

## Resource Usage

### Memory

- Backend: 500MB-2GB (depends on users)
- Database: 200MB-1GB
- Redis: 100MB-500MB
- Client: 20MB
- MinIO: 100MB

**Total:** 1-4GB typical

### CPU

- Idle: Minimal (<5% per core)
- Active: 10-50% per core (depends on load)

### Disk

- Database: Grows with messages, files, activity
- MinIO: Grows with uploaded files
- Typical: 10GB-100GB+

## Security Model

### Network Isolation

- External internet can only reach ports 80, 443
- Database, Redis, MinIO are on internal network only
- Services communicate by hostname (DNS resolution in docker network)

### Authentication

- Frontend -> Backend: JWT token in header/cookie
- Backend -> Database: Username/password (hardcoded in config)
- Backend -> Redis: Password (in config)
- Backend -> MinIO: Access key/secret (in config)

### Encryption

- TLS/HTTPS: All external communication encrypted
- Message encryption: E2E encryption in database (AES-GCM)
- Database: Passwords stored hashed (Argon2)

## Monitoring Points

### Health Checks

- Backend: `GET /health`
- Database: `pg_isready`
- Redis: `redis-cli ping`
- MinIO: HTTP health endpoint

### Metrics

- Backend: `GET /metrics` (Prometheus format)
- Docker: `docker stats`
- Logs: `docker compose logs`

## Next Steps

- [Production Setup](production-setup.md) - Production deployment
- [Development Workflow](development-workflow.md) - Development setup
- [Troubleshooting](troubleshooting.md) - Debug issues
