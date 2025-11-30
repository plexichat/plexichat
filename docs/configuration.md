# Server Configuration

PlexiChat server configuration guide.

## Configuration Files

Configuration is loaded from (in priority order):
1. `plexichat/config/config.yaml` (project directory)
2. `~/.plexichat/config/config.yaml` (home directory)

If no config file exists, defaults are used and a config file is created.

## Configuration Sections

### Server

```yaml
server:
  host: 0.0.0.0          # Bind address
  port: 8000             # Listen port
  workers: 1             # Number of worker processes
  reload: false          # Auto-reload on code changes (dev only)
```

Environment variable overrides:
- `HOST` - Override server host
- `PORT` - Override server port

### Database

```yaml
database:
  type: sqlite           # sqlite or postgres
  path: ~/.plexichat/data/plexichat.db  # SQLite path
  
  # PostgreSQL settings (only used when type: postgres)
  postgres:
    host: localhost
    port: 5432
    user: postgres
    password: ""
    dbname: plexichat
```

#### PostgreSQL Setup

To use PostgreSQL instead of SQLite:

1. Install the PostgreSQL driver:
   ```bash
   pip install psycopg2-binary
   ```

2. Create the database:
   ```sql
   CREATE DATABASE plexichat;
   ```

3. Update your config:
   ```yaml
   database:
     type: postgres
     postgres:
       host: localhost
       port: 5432
       user: postgres
       password: your_secure_password
       dbname: plexichat
   ```

The database module automatically handles differences between SQLite and PostgreSQL, including placeholder syntax conversion (`?` to `%s`).

### Authentication

```yaml
authentication:
  jwt:
    secret_key: CHANGE_THIS_IN_PRODUCTION  # MUST change in production
    algorithm: HS256
    access_token_expire_minutes: 30
    refresh_token_expire_days: 7
  
  password:
    min_length: 8
    require_uppercase: true
    require_lowercase: true
    require_digit: true
    require_special: true
  
  account_lockout:
    max_failed_attempts: 5
    lockout_duration_minutes: 15
  
  session:
    max_concurrent_sessions: 3
```

### Encryption

```yaml
encryption:
  argon2:
    time_cost: 2
    memory_cost: 65536
    parallelism: 2
    hash_length: 32
    salt_length: 16
  
  aes_gcm:
    key_length: 32
    nonce_length: 12
    tag_length: 16
  
  snowflake:
    epoch: "2024-01-01T00:00:00Z"
    worker_id: 1
    datacenter_id: 1
```

### API

```yaml
api:
  title: PlexiChat API
  version: a.1.0-1
  api_prefix: /api/v1
  debug: false           # Enable debug mode
  cors_origins:
    - "*"                # Allowed CORS origins
  docs_url: /docs        # Swagger UI path
  redoc_url: /redoc      # ReDoc path
```

### Logging

```yaml
logging:
  level: INFO            # DEBUG, INFO, WARNING, ERROR
  max_bytes: 10485760    # Max log file size (10MB)
  backup_count: 5        # Number of backup files
  zip_logs: true         # Compress rotated logs
  rotate: true           # Enable log rotation
```

### Storage

```yaml
storage:
  data_dir: ~/.plexichat/data
  logs_dir: ~/.plexichat/logs
  media_dir: ~/.plexichat/media
  temp_dir: ~/.plexichat/temp
```

### Application

```yaml
application:
  name: PlexiChat
  version: a.1.0-1
  environment: development  # development, staging, production
```

### Versioning

```yaml
versioning:
  min_supported_version: a.1.0-1  # Minimum client version
  update_url: null                 # URL for client updates
```

### Documentation Server

```yaml
docs:
  enabled: true
  path: /docs/api
  title: PlexiChat API Documentation
  
  # Base URLs shown in documentation
  base_url: https://api.example.com
  websocket_url: wss://gateway.example.com
  
  # Theme
  theme:
    style: dark
    primary_color: "#e94560"
  
  # Rate limiting for docs
  rate_limit:
    enabled: true
    requests: 60
    window_seconds: 60
  
  # Caching
  cache:
    enabled: true
    ttl_seconds: 300
  
  # Security
  security:
    require_auth: false  # Public docs by default
```

## Rate Limits

Rate limits are configured per-endpoint. See [Rate Limits](rate-limits.md) for details.

## Production Checklist

Before deploying to production:

1. **Change JWT secret key**
   ```yaml
   authentication:
     jwt:
       secret_key: <generate-secure-random-key>
   ```

2. **Disable debug mode**
   ```yaml
   api:
     debug: false
   ```

3. **Set environment**
   ```yaml
   application:
     environment: production
   ```

4. **Configure CORS**
   ```yaml
   api:
     cors_origins:
       - https://your-domain.com
   ```

5. **Use PostgreSQL for production**
   ```yaml
   database:
     type: postgres
     postgres:
       host: your-db-host
       port: 5432
       user: postgres
       password: <secure-password>
       dbname: plexichat
   ```
   
   Install the driver: `pip install psycopg2-binary`

6. **Set appropriate log level**
   ```yaml
   logging:
     level: WARNING
   ```

## Environment Variables

| Variable | Config Path | Description |
|----------|-------------|-------------|
| `HOST` | `server.host` | Server bind address |
| `PORT` | `server.port` | Server port |
| `DATABASE_URL` | - | Full database URL (overrides config) |
| `JWT_SECRET` | `authentication.jwt.secret_key` | JWT signing key |
| `LOG_LEVEL` | `logging.level` | Log level |
