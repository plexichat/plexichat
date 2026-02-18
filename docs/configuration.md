# Server Configuration

PlexiChat server configuration guide.

## Configuration Files

Configuration is loaded from (in priority order):
1. `plexichat/config/config.yaml` (project directory)
2. `~/.plexichat/config/config.yaml` (home directory)

If no config file exists, defaults are used and a config file is created.

## API Base URL

The API base URL is dynamically determined based on your deployment:

| Environment | Base URL |
|-------------|----------|
| Production | `https://plexichat-app.tail79f345.ts.net/api/v1` |
| Development | `http://localhost:8000/api/v1` |

All API endpoints are relative to this base URL. For example, `GET /api/v1/users/@me` becomes `https://plexichat-app.tail79f345.ts.net/api/v1/users/@me` in production.

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
    sslmode: prefer      # disable, allow, prefer, require, verify-ca, verify-full
  
  connection_pool:
    min_connections: 20
    max_connections: 100
    connect_timeout: 10
    max_idle_time: 120
    validation_interval: 60
    enable_validation: true
    validation_query: "SELECT 1"
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
       sslmode: prefer
   ```

4. Or use the `DATABASE_URL` environment variable:
   ```bash
   export DATABASE_URL="postgres://user:password@host:port/dbname"
   ```

The database module automatically handles differences between SQLite and PostgreSQL, including placeholder syntax conversion (`?` to `%s`).

### Authentication

```yaml
authentication:
  accounts:
    allow_registration: true
    require_email_verification: false
    max_bots_per_user: 5
    age_gate_enabled: false      # Enable age verification on registration
    minimum_age: 13             # Minimum required age
    # Verification type: "boolean" (simple check) or "dob" (store date of birth)
    age_verification_type: "boolean"
  
  sessions:
    token_bytes: 32
    expire_hours: 168
    max_per_user: 10
    extend_on_activity: true
  
  security:
    max_failed_attempts: 5
    lockout_duration_minutes: 15
    token_cache_ttl: 30
    token_verify_rate_limit: 100
    token_binding: false
```

### Applications

```yaml
applications:
  max_applications_per_user: 25
  max_commands_per_app: 100
  interaction_timeout: 900
  webhook_signature_secret: null  # Auto-generated on startup if not set
  oauth:
    code_expiry_seconds: 600
    token_expiry_seconds: 604800
    refresh_enabled: true
  rate_limits:
    requests_per_minute: 60
```

### Polls

```yaml
polls:
  max_options: 10
  min_options: 2
  max_question_length: 300
  max_option_length: 100
  min_duration_hours: 1
  max_duration_hours: 168
```

### Emojis

```yaml
emojis:
  max_emojis_per_server: 50
  max_animated_emojis_per_server: 50
  max_emoji_size: 262144
  emoji_min_name_length: 2
  emoji_max_name_length: 32
  allowed_formats: ["image/png", "image/jpeg", "image/gif", "image/webp"]
```

### Search

```yaml
search:
  enabled: true
  backend: sqlite_fts5           # sqlite_fts5 or elasticsearch
  result_limit: 100
  batch_size: 100
  write_time_indexing: true
  discovery:
    enabled: true
    min_members_for_listing: 10
    max_tags: 10
    bump_cooldown_hours: 4
```

### Servers

```yaml
servers:
  server_name_min_length: 2
  server_name_max_length: 100
  channel_name_max_length: 100
  role_name_max_length: 100
  invite_code_length: 8
  events:
    max_event_duration_hours: 168
    max_recurring_instances: 50
  onboarding:
    max_onboarding_steps: 10
    max_welcome_channels: 5
    max_step_options: 25
  templates:
    template_code_length: 8
    max_channels_in_template: 100
    max_roles_in_template: 50
    max_templates_per_user: 25
```

### OAuth

OAuth allows users to sign in using external identity providers (Google, GitHub, Microsoft).

```yaml
oauth:
  # State token TTL in seconds (default: 600 = 10 minutes)
  # Controls how long users have to complete the OAuth flow
  state_ttl_seconds: 600
  
  # Entropy for state tokens in bytes (minimum 32 recommended)
  state_token_bytes: 32
  
  # Entropy for OIDC nonce in bytes (minimum 32 recommended)
  nonce_token_bytes: 32
  
  # Clean up expired states on each verification (recommended: true)
  cleanup_on_verify: true
  
  # Maximum pending OAuth states per IP address (0 = unlimited)
  # Helps prevent state flooding attacks
  max_states_per_ip: 10
  
  # Enable PKCE (Proof Key for Code Exchange) for supported providers
  # Recommended: true for better security against authorization code interception
  pkce_enabled: true
  
  # PKCE configuration
  pkce:
    # Length of random bytes for code verifier (32-96, default 64)
    verifier_length: 64
    # Minimum verifier length per RFC 7636 (do not change unless required)
    min_verifier_length: 43
    # Maximum verifier length per RFC 7636 (do not change unless required)
    max_verifier_length: 128
  
  # Provider configurations
  google:
    client_id: "YOUR_GOOGLE_CLIENT_ID"
    client_secret: "YOUR_GOOGLE_CLIENT_SECRET"
  github:
    client_id: "YOUR_GITHUB_CLIENT_ID"
    client_secret: "YOUR_GITHUB_CLIENT_SECRET"
  microsoft:
    client_id: "YOUR_MICROSOFT_CLIENT_ID"
    client_secret: "YOUR_MICROSOFT_CLIENT_SECRET"
```

#### Security Features

- **Server-side State Storage**: CSRF protection tokens are stored in the database, preventing state forgery attacks
- **PKCE Support**: Proof Key for Code Exchange prevents authorization code interception (enabled for Google and Microsoft)
- **Nonce Support**: OpenID Connect nonce for replay attack prevention (Google and Microsoft)
- **Single-use States**: Each state token can only be used once, preventing replay attacks
- **State Expiration**: States expire after 10 minutes by default
- **IP Rate Limiting**: Limits pending OAuth states per IP to prevent flooding attacks

#### Environment Variables

For production, provide secrets via environment variables:
- `OAUTH_GOOGLE_CLIENT_SECRET`
- `OAUTH_GITHUB_CLIENT_SECRET`
- `OAUTH_MICROSOFT_CLIENT_SECRET`

#### Setting Up OAuth Providers

**Google:**
1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a project and enable the Google+ API
3. Create OAuth 2.0 credentials (Web application)
4. Add authorized redirect URI: `https://your-domain.com/oauth/callback/google`

**GitHub:**
1. Go to [GitHub Developer Settings](https://github.com/settings/developers)
2. Create a new OAuth App
3. Set Authorization callback URL: `https://your-domain.com/oauth/callback/github`

**Microsoft:**
1. Go to [Azure Portal](https://portal.azure.com/)
2. Register an application in Azure AD
3. Add redirect URI: `https://your-domain.com/oauth/callback/microsoft`
4. Use "common" tenant for personal + work accounts

### Encryption

```yaml
encryption:
  key_rotation_days: 90  # Rotate encryption keys every 90 days (0 to disable)
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
  description: REST API for PlexiChat messaging platform
  version: a.1.0-44
  api_prefix: /api/v1
  debug: false           # Enable debug mode
  cors_origins:
    - "*"                # Allowed CORS origins
  cors_allow_credentials: true
  cors_allow_methods:
    - "*"
  cors_allow_headers:
    - "*"
  # Trusted proxies for IP extraction (e.g., ["10.0.0.1", "172.16.0.0/12"])
  trusted_proxies: []
  # Whether to trust X-Forwarded-For (requires trusted_proxies for security)
  trust_x_forwarded_for: false
  docs_url: /docs        # Swagger UI path (null to disable)
  redoc_url: /redoc      # ReDoc path (null to disable)
  openapi_url: /openapi.json  # OpenAPI schema path
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
  version: a.1.0-44
  environment: development  # development, staging, production
```

### Versioning

```yaml
versioning:
  min_supported_version: a.1.0-44  # Minimum client version
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

### Rate Limiting

```yaml
rate_limiting:
  # Secret for bypassing rate limits (e.g., for internal services)
  # Auto-generated on startup if not set.
  bypass_secret: null
```

### Messaging

```yaml
messaging:
  max_message_length: 4000
  max_group_participants: 100
  max_attachment_size: 10485760  # 10MB
  max_attachments_per_message: 10
  dm_auto_create: true
  encrypt_messages: true         # Enable message encryption at rest
  encrypt_attachments: true      # Enable attachment URL encryption
  message_preview_length: 100
```

#### Message Encryption

When `encrypt_messages` is enabled, message content is encrypted using AES-256-GCM before being stored in the database. This provides **Zero-friction At-Rest Encryption** (server-side encryption), protecting data from database compromises.

**Security Keys:**
- System Keyring: `~/.plexichat/data/system_system_keyring.json`
- Message Keyring: `~/.plexichat/data/message_system_keyring.json`
- Machine Key: `~/.plexichat/data/.machine_key`

**Environment Overrides:**
For production deployments, you can provide these keys via environment variables (Base64 encoded 32-byte strings):
- `PLEXICHAT_ENCRYPTION_KEY`
- `PLEXICHAT_MESSAGE_KEY`
- `PLEXICHAT_SYSTEM_KEY` (Master key override)

**Important:** Back up your encryption keyring files! If lost, encrypted data cannot be recovered.

#### Key Rotation

PlexiChat supports automatic encryption key rotation. When `key_rotation_days` is set, the server will automatically generate a new encryption key if the current one is older than the specified period.

- Old keys are kept in the keyring to allow decryption of existing data.
- All new data is encrypted using the latest key version.
- Key rotation period is checked on server startup.

The server will show a security warning on startup reminding you to back up the keyring.

### User Features

```yaml
user_features:
  alpha_registration_enabled: true  # Allow new users to register as alpha testers
  default_tier: standard
  badge_display_limit: 5
  available_badges:
    - alpha_tester
    - early_supporter
    - staff
    - verified
  rate_limit_tiers:
    standard:
      multiplier: 1.0
      max_voice_minutes_per_day: 120
      max_file_uploads_per_day: 50
      max_file_size_mb: 10
    alpha:
      multiplier: 2.0
      max_voice_minutes_per_day: 480
      max_file_uploads_per_day: 200
      max_file_size_mb: 25
    premium:
      multiplier: 3.0
      max_voice_minutes_per_day: -1  # Unlimited
      max_file_uploads_per_day: 500
      max_file_size_mb: 100
```

### Media Storage

```yaml
media:
  storage_backend: local         # local or s3
  signing_key: CHANGE_THIS       # Key for signing media URLs
  
  # S3/MinIO settings (when storage_backend: s3)
  s3_bucket: plexichat
  s3_access_key: your_access_key
  s3_secret_key: your_secret_key
  s3_region: us-east-1
  s3_endpoint: http://localhost:9000  # For MinIO
  s3_public_url: http://localhost:9000/plexichat
  
  size_limits:
    avatar: 5242880              # 5MB
    attachment: 10485760         # 10MB
    server_icon: 5242880         # 5MB
```

### Voice/Video

```yaml
voice:
  enabled: true
  sfu_backend: mediasoup         # mediasoup or janus
  mediasoup_url: https://localhost:4443
  stun_urls:
    - stun:stun.l.google.com:19302
  turn_urls:
    - turn:your-turn-server:3478
  turn_username: username
  turn_credential: password
  turn_secret: your_turn_secret  # For time-limited credentials
  log_connections: true
  log_quality_metrics: true
```

### Self-Test

Automated API validation suite.

```yaml
selftest:
  enabled: false          # Allow running self-tests
  run_on_startup: false   # Run test suite once when server starts
  exit_on_failure: false  # Exit server process if any test fails
  capture_stack_traces: true  # Capture server tracebacks for localhost failures
  retry_on_failure: true  # Retry failed endpoints with debug headers
  excluded_endpoints:     # Endpoints to skip
    - "/api/v1/auth/logout"
  test_user:              # User to create for testing
    username: "selftest_admin"
    email: "selftest@internal.local"
    password: "SelfTest_Password_123!"
```

## Rate Limits

Rate limits are configured per-endpoint. See [Rate Limits](rate-limits.md) for details.

## Production Checklist

Before deploying to production:

1. **Disable debug mode**
   ```yaml
   api:
     debug: false
   ```

2. **Set environment**
   ```yaml
   application:
     environment: production
   ```

3. **Configure CORS**
   ```yaml
   api:
     cors_origins:
       - https://your-domain.com
   ```

4. **Use PostgreSQL for production**
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

5. **Set appropriate log level**
   ```yaml
   logging:
     level: WARNING
   ```

6. **Back up encryption keyring**
   
   The encryption keyring is stored at `~/.plexichat/data/system_keyring.json`. 
   Back this up securely - if lost, encrypted data cannot be recovered.

7. **Set media signing key**
   ```yaml
   media:
     signing_key: <generate-secure-random-key>
   ```

8. **Configure TURN secret (for voice)**
   ```yaml
   voice:
     turn_secret: <your-turn-server-secret>
   ```

## Security Warnings

On startup, the server checks for default/placeholder security keys and logs warnings:

- `media.signing_key` - Used for signing media URLs
- `voice.turn_secret` - Used for TURN server authentication  
- `encryption.keyring` - Auto-generated, but should be backed up
- `redis.password` - Should be set if Redis is enabled
- `database.postgres.password` - Should be strong for PostgreSQL

## Environment Variables

| Variable | Config Path | Description |
|----------|-------------|-------------|
| `HOST` | `server.host` | Server bind address |
| `PORT` | `server.port` | Server port |
| `DATABASE_URL` | `database.*` | Full database URL (overrides config) |
| `LOG_LEVEL` | `logging.level` | Log level (DEBUG, INFO, WARNING, ERROR) |

### Database URL Format

The `DATABASE_URL` environment variable supports two formats:

**SQLite:**
```
sqlite:///path/to/database.db
sqlite:///~/.plexichat/data/plexichat.db
```

**PostgreSQL:**
```
postgres://user:password@host:port/dbname
postgres://user:password@host:port/dbname?sslmode=require
postgresql://user:password@host:port/dbname
```

When `DATABASE_URL` is set, it overrides the `database.type`, `database.path`, and `database.postgres.*` settings from the config file.
