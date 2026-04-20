# Default Configuration Reference

This document provides a comprehensive reference for all configuration options in Plexichat. Each setting is explained with its default value, purpose, and implications for your deployment.

## Complete Default Configuration

Copy this complete configuration file as your starting point. Save it as `config/config.yaml` in your project directory or `~/.plexichat/config/config.yaml` in your home directory, then modify the values according to your deployment requirements.

```yaml
# Plexichat Default Configuration
# Copy this file to config/config.yaml or ~/.plexichat/config/config.yaml
# Modify values according to your deployment needs

application:
  name: "Plexichat"
  version: "a.1.0-51"
  environment: "development"

server:
  host: "127.0.0.1"
  port: 8000
  workers: 1
  reload: false

logging:
  level: "INFO"
  format: "json"
  file: ""
  console: true

database:
  type: "sqlite"
  path: "data/database.db"
  postgres:
    host: "localhost"
    port: 5432
    user: "postgres"
    password: ""
    dbname: "plexichat"
    sslmode: "prefer"
  connection_pool:
    min_connections: 2
    max_connections: 20
    connect_timeout: 10
  monitoring:
    slow_query_threshold_ms: 1000
    alert_on_slow_queries: true
  migrations:
    auto_migrate: true
    migration_dir: "migrations"

redis:
  enabled: false
  host: "localhost"
  port: 6379
  password: ""
  db: 0
  ssl: false
  ssl_cert_reqs: "required"
  ssl_ca_certs: ""
  connection_pool:
    max_connections: 50
    timeout: 5
  key_prefix: "plexichat:"
  ttl:
    session: 1800
    presence: 300
    cache: 60
  cache_max_items: 1000

authentication:
  password:
    min_length: 12
    max_length: 128
    require_uppercase: true
    require_lowercase: true
    require_digit: true
    require_special: true
  accounts:
    username_min_length: 3
    username_max_length: 32
    username_pattern: "^[a-zA-Z0-9_]+$"
    email_validation:
      strict: true
      allow_custom_tlds: false
      valid_tlds: []
  sessions:
    max_sessions_per_user: 10
    session_lifetime_seconds: 2592000
    refresh_token_lifetime_seconds: 7776000
    device_tracking_enabled: true
  totp:
    enabled: true
    issuer: "Plexichat"
    digits: 6
    interval: 30
    backup_code_count: 10
    backup_code_length: 8
    backup_code_max_checks: 3
  account_deletion:
    enabled: true
    grace_period_days: 30
    anonymize_content: true
    audit_log:
      enabled: true
      file_path: "/var/lib/plexichat/audit/deletion_log.jsonl"
      hash_chain_enabled: true
    reaper:
      interval_hours: 24
      batch_size: 50
      boot_check_enabled: true
  security:
    max_login_attempts: 5
    lockout_duration_seconds: 900
    password_change_cooldown_seconds: 86400
  registration:
    enabled: true
    require_email_verification: false
    default_role: "user"

api:
  prefix: "/api/v1"
  docs_url: "/docs"
  redoc_url: "/redoc"
  openapi_url: "/openapi.json"
  cors:
    enabled: true
    allow_origins: ["*"]
    allow_methods: ["*"]
    allow_headers: ["*"]
    allow_credentials: false
    max_age: 600
  max_request_body_size: 10485760
  debug: false

websocket:
  enabled: true
  path: "/gateway"
  max_connections: 10000
  heartbeat_interval_seconds: 30
  heartbeat_timeout_seconds: 60
  max_message_size: 1048576
  max_decompressed_size: 10485760
  compression:
    enabled: true
    level: 6
  rate_limit:
    enabled: true
    connections_per_ip: 5
    messages_per_second: 50

messaging:
  max_message_length: 2000
  max_attachments_per_message: 10
  max_embeds_per_message: 10
  max_mentions_per_message: 20
  edit_time_limit_seconds: 600
  delete_time_limit_seconds: 3600
  threads:
    enabled: true
    max_thread_depth: 3
  reactions:
    enabled: true
    max_reactions_per_message: 20
    max_custom_reactions_per_message: 10

media:
  storage_backend: "local"
  local:
    path: "data/media"
    url_prefix: "/media"
  s3:
    bucket: ""
    region: "us-east-1"
    access_key_id: ""
    secret_access_key: ""
    endpoint_url: ""
    public_url: ""
  max_file_size: 104857600
  max_total_size_per_user: 10737418240
  allowed_types:
    images: ["image/jpeg", "image/png", "image/gif", "image/webp"]
    videos: ["video/mp4", "video/webm"]
    audio: ["audio/mpeg", "audio/ogg", "audio/wav"]
    documents: ["application/pdf", "text/plain"]
  processing:
    resize_images: true
    max_image_width: 4096
    max_image_height: 4096
    thumbnail_size: 300
    compress_videos: false
  malware_scanning:
    enabled: false
    clamav_socket: "/var/run/clamav/clamd.ctl"
  rate_limit:
    uploads_per_minute: 10
    thumbnails_per_minute: 30
  external_proxy:
    enabled: false
    timeout_seconds: 10

rate_limiting:
  enabled: true
  global:
    requests: 100
    window_seconds: 60
    burst: 50
  user:
    requests: 50
    window_seconds: 60
    burst: 25
  ip:
    requests: 30
    window_seconds: 60
    burst: 15
  route:
    "/api/v1/auth/login":
      requests: 5
      window_seconds: 60
      burst: 2
  bot_multiplier: 0.5
  webhook_multiplier: 2.0
  bypass_internal: true
  bypass_admin: true

servers:
  max_name_length: 100
  max_members: 250000
  max_channels_per_server: 500
  max_roles_per_server: 250
  onboarding:
    max_onboarding_steps: 10
    max_welcome_channels: 5
    max_step_options: 25
  templates:
    max_templates_per_user: 25
    template_code_length: 8
    max_channels_in_template: 100
    max_roles_in_template: 50
  events:
    enabled: true
    retention_days: 90

user_features:
  alpha_registration_enabled: false
  rate_limit_tiers:
    standard:
      multiplier: 1.0
      max_voice_minutes_per_day: 120
      max_video_minutes_per_day: 60
      max_file_uploads_per_day: 50
      max_file_size_mb: 10
      max_servers: 100
    alpha:
      multiplier: 2.0
      max_voice_minutes_per_day: 480
      max_video_minutes_per_day: 240
      max_file_uploads_per_day: 200
      max_file_size_mb: 25
      max_servers: 200
    premium:
      multiplier: 3.0
      max_voice_minutes_per_day: -1
      max_video_minutes_per_day: -1
      max_file_uploads_per_day: 500
      max_file_size_mb: 100
      max_servers: 500
  default_tier: "standard"
  badge_display_limit: 5
  available_badges:
    - alpha_tester
    - early_supporter
    - staff
    - verified
    - bug_hunter
    - contributor

voice:
  enabled: true
  sfu_backend: "aiortc"
  mediasoup_url: "wss://localhost:4443"
  mediasoup_origin: "https://localhost"
  janus_url: "http://localhost:8088/janus"
  stun_urls:
    - "stun:stun.l.google.com:19302"
  turn_urls: []
  turn_secret: ""
  turn_ttl: 86400
  turn_username: ""
  turn_credential: ""
  max_bitrate: 128000
  max_participants_per_channel: 25

search:
  enabled: true
  backend: "sqlite"
  elasticsearch:
    hosts: ["http://localhost:9200"]
    index_prefix: "plexichat"
  meilisearch:
    host: "http://localhost:7700"
    index_prefix: "plexichat"
  batch_size: 100
  write_time_indexing: true
  result_limit: 100
  min_query_length: 2
  max_query_length: 200
  discovery:
    enabled: true
    min_members: 50
    cooldown_hours: 24

oauth:
  authorization_endpoint: "/oauth2/authorize"
  token_endpoint: "/oauth2/token"
  scopes:
    - "identify"
    - "email"
    - "guilds"
    - "bot"
  token_expiry_seconds: 3600
  code_expiry_seconds: 600
  refresh_enabled: true

encryption:
  algorithm: "argon2id"
  argon2:
    time_cost: 3
    memory_cost: 65536
    parallelism: 4
    salt_length: 16
    hash_length: 32

monitoring:
  enabled: false
  prometheus:
    enabled: false
    port: 9090
    path: "/metrics"
  health_check:
    enabled: true
    path: "/health"
  metrics:
    enabled: true
    collect_runtime: true
    collect_database: true
    collect_redis: true

tls:
  cert_path: ""
  key_path: ""
  cert_days: 365
  auto_generate: true

email:
  enabled: false
  smtp:
    host: "localhost"
    port: 587
    use_tls: true
    username: ""
    password: ""
    from_address: "noreply@plexichat.example"
    from_name: "Plexichat"

presence:
  timeout_ms: 300000
  typing_timeout_ms: 6000
  custom_status_max_length: 128
  custom_status_emoji_enabled: true

polls:
  enabled: true
  min_options: 2
  max_options: 10
  min_duration_hours: 1
  max_duration_hours: 168
  max_question_length: 300
  max_option_length: 100

emojis:
  enabled: true
  max_emojis_per_server: 50
  max_emoji_name_length: 32
  max_emoji_size_kb: 256
  animated_enabled: true

stickers:
  enabled: true
  max_packs_per_server: 10
  max_stickers_per_pack: 50
  max_pack_name_length: 50
  max_sticker_name_length: 30
  allowed_formats:
    - "png"
    - "apng"
    - "json"
  max_sticker_size_kb: 512

soundboard:
  enabled: true
  max_sounds_per_server: 50
  max_sound_name_length: 30
  max_sound_size_mb: 2
  max_sound_duration_seconds: 10
  allowed_formats:
    - "mp3"
    - "ogg"
    - "wav"
  default_volume: 1.0

webhooks:
  enabled: true
  max_webhooks_per_server: 10
  max_webhook_name_length: 80
  rate_limit_per_minute: 120
  retry_attempts: 3
  retry_delay_seconds: 5
  timeout_seconds: 10

automod:
  enabled: true
  default_actions:
    - "delete_message"
    - "alert_moderators"
  rate_limit_window: 60
  reputation_decay_rate: 1.0
  reputation_decay_interval: 86400
  max_violations_before_action: 1
  ai:
    openai:
      api_key: ""
      model: "gpt-4"
    perspective:
      api_key: ""
    custom:
      endpoint_url: ""
      api_key: ""

applications:
  max_applications_per_user: 100
  max_bot_accounts_per_user: 10
  bot_prefix_required: true
  token_expiry_seconds: 3600
  code_expiry_seconds: 600
  refresh_enabled: true

admin_ui:
  enabled: true
  path: "/admin"
  require_2fa: true
  session_timeout_minutes: 30

embeds:
  enabled: true
  max_embeds_per_message: 10
  max_title_length: 256
  max_description_length: 4096
  max_fields: 25
  max_field_name_length: 256
  max_field_value_length: 1024
  url_preview:
    enabled: true
    timeout_seconds: 8
    max_html_bytes: 524288
    max_redirects: 5
    max_image_size: 5242880
    cache_ttl_seconds: 3600
    rate_limit_requests: 10
    rate_limit_window_seconds: 60
    proxy_images: true
    allowed_schemes:
      - "http"
      - "https"

telemetry:
  enabled: false
  endpoint: "https://telemetry.plexichat.example"
  include_version: true
  include_environment: false
  include_instance_id: true

selftest:
  enabled: true
  run_on_startup: true
  database:
    enabled: true
  redis:
    enabled: true
  media:
    enabled: true
    test_upload: false

docs:
  enabled: true
  path: "/docs"

feedback:
  enabled: true
  email: "feedback@plexichat.example"
  max_length: 5000

reports:
  enabled: true
  max_reports_per_user: 10
  cooldown_hours: 24

avatars:
  enabled: true
  max_size_kb: 1024
  default_colors:
    - "#5865F2"
    - "#57F287"
    - "#FEE75C"
    - "#EB459E"
    - "#ED4245"
    - "#9B59B6"
    - "#3498DB"
    - "#1ABC9C"
    - "#E67E22"
    - "#95A5A6"
```

---

## Configuration File Locations

Plexichat looks for configuration files in the following order (first found wins):
1. `./config/config.yaml` (relative to application directory)
2. `~/.plexichat/config/config.yaml` (user home directory)
3. Built-in defaults (this document)

## Quick Start Example

```yaml
application:
  name: Plexichat
  environment: production

server:
  host: 0.0.0.0
  port: 8000
  workers: 4

database:
  type: postgres
  postgres:
    host: localhost
    port: 5432
    user: plexichat
    password: your_secure_password
    dbname: plexichat

redis:
  enabled: true
  host: localhost
  port: 6379
```

---

## Application Settings

Basic application identification and runtime environment.

```yaml
application:
  name: "Plexichat"
  version: "a.1.0-51"
  environment: "development"
```

- **name**: Application name displayed in UI and logs
- **version**: Application version (auto-set from package, do not modify)
- **environment**: Runtime environment. Options: `development`, `staging`, `production`. Affects logging verbosity and error reporting.

---

## Server Settings

HTTP server configuration for the FastAPI application.

```yaml
server:
  host: "127.0.0.1"
  port: 8000
  workers: 1
  reload: false
```

- **host**: IP address to bind to. Use `0.0.0.0` for public access, `127.0.0.1` for local only.
- **port**: TCP port for HTTP server.
- **workers**: Number of worker processes for handling requests. Increase for production (typically 2-4x CPU cores).
- **reload**: Enable auto-reload on file changes (development only, never use in production).

---

## Logging Settings

Control logging verbosity and output destinations.

```yaml
logging:
  level: "INFO"
  format: "json"
  file: ""
  console: true
```

- **level**: Minimum log level. Options: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`. DEBUG is verbose, recommended for development. INFO for production.
- **format**: Log format. Options: `json` (structured logs), `text` (human-readable). JSON recommended for production log aggregation.
- **file**: Path to log file. If empty, logs only to console.
- **console**: Enable console output. Disable when using file output only in production.

---

## Database Settings

Configure the database backend for data persistence. For detailed guidance on database selection, connection pooling, and scaling, see [Database Configuration](config-database.md).

```yaml
database:
  type: "sqlite"
  path: "data/database.db"
  postgres:
    host: "${POSTGRES_HOST:-localhost}"
    port: 5432
    user: "${POSTGRES_USER:-postgres}"
    password: "${POSTGRES_PASSWORD}"
    dbname: "${POSTGRES_DBNAME:-plexichat}"
    sslmode: "${POSTGRES_SSLMODE:-prefer}"
  connection_pool:
    min_connections: 2
    max_connections: 20
    connect_timeout: 10
  monitoring:
    slow_query_threshold_ms: 1000
    alert_on_slow_queries: true
  migrations:
    auto_migrate: true
    migration_dir: "migrations"
```

### Database Type

- **type**: Database engine. Options: `sqlite` (file-based, no setup), `postgres` (recommended for production).

### SQLite Configuration

- **path**: Filesystem path to SQLite database file. Directory is created automatically.

### PostgreSQL Configuration

- **host**: PostgreSQL server hostname or IP.
- **port**: PostgreSQL server port.
- **user**: Database user with create/read/write permissions.
- **password**: Database user password. Keep secure and out of version control.
- **dbname**: Database name (will be created if it doesn't exist).
- **sslmode**: SSL/TLS mode. Options: `disable` (no encryption), `allow` (try SSL, accept plain), `prefer` (try SSL, accept plain), `require` (require SSL), `verify-ca` (verify CA), `verify-full` (verify CA and hostname). Use `require` or higher in production.

### Connection Pool

- **min_connections**: Minimum idle connections to maintain. Reduces connection setup overhead.
- **max_connections**: Maximum concurrent connections. Too high can overwhelm database; too low causes contention.
- **connect_timeout**: Seconds to wait for connection before failing.

### Monitoring

- **slow_query_threshold_ms**: Query duration in milliseconds that triggers a slow query warning.
- **alert_on_slow_queries**: Enable logging of slow queries for performance monitoring.

### Migrations

- **auto_migrate**: Automatically apply pending database migrations on startup. Disable if you prefer manual control.
- **migration_dir**: Directory containing SQL migration files.

---

## Redis Settings

Redis configuration for caching, session storage, and pub/sub messaging. For detailed guidance on Redis setup, scaling, and monitoring, see [Redis Configuration](config-redis.md).

```yaml
redis:
  enabled: false
  host: "${REDIS_HOST:-localhost}"
  port: 6379
  password: "${REDIS_PASSWORD}"
  db: 0
  ssl: false
  ssl_cert_reqs: "required"
  ssl_ca_certs: ""
  connection_pool:
    max_connections: 50
    timeout: 5
  key_prefix: "plexichat:"
  ttl:
    session: 1800
    presence: 300
    cache: 60
  cache_max_items: 1000
```

### Basic Settings

- **enabled**: Enable Redis functionality. If disabled, application uses in-memory fallbacks (not suitable for multi-worker deployments).
- **host**: Redis server hostname.
- **port**: Redis server port.
- **password**: Redis authentication password.
- **db**: Redis database number (0-15).

### SSL/TLS

- **ssl**: Enable SSL/TLS for Redis connection.
- **ssl_cert_reqs**: SSL certificate requirement. Options: `none`, `optional`, `required`.
- **ssl_ca_certs**: Path to CA certificates file for SSL verification.

### Connection Pool

- **max_connections**: Maximum concurrent Redis connections.
- **timeout**: Connection timeout in seconds.

### Key Management

- **key_prefix**: Prefix added to all Redis keys to avoid collisions with other applications.

### TTL (Time-To-Live)

- **session**: Default TTL for session data in seconds (1800 = 30 minutes).
- **presence**: Default TTL for presence data in seconds (300 = 5 minutes).
- **cache**: Default TTL for generic cache data in seconds (60 = 1 minute).

### Cache Limits

- **cache_max_items**: Maximum number of items per cache category before eviction.

---

## Authentication Settings

User authentication, passwords, sessions, 2FA, and account deletion configuration. For detailed guidance on security policies, session management, and GDPR compliance, see [Authentication Configuration](config-authentication.md).

```yaml
authentication:
  password:
    min_length: 12
    max_length: 128
    require_uppercase: true
    require_lowercase: true
    require_digit: true
    require_special: true
  accounts:
    username_min_length: 3
    username_max_length: 32
    username_pattern: "^[a-zA-Z0-9_]+$"
    email_validation:
      strict: true
      allow_custom_tlds: false
      valid_tlds: []
  sessions:
    max_sessions_per_user: 10
    session_lifetime_seconds: 2592000
    refresh_token_lifetime_seconds: 7776000
    device_tracking_enabled: true
  totp:
    enabled: true
    issuer: "Plexichat"
    digits: 6
    interval: 30
    backup_code_count: 10
    backup_code_length: 8
    backup_code_max_checks: 3
  account_deletion:
    enabled: true
    grace_period_days: 30
    anonymize_content: true
    audit_log:
      enabled: true
      file_path: "/var/lib/plexichat/audit/deletion_log.jsonl"
      hash_chain_enabled: true
    reaper:
      interval_hours: 24
      batch_size: 50
      boot_check_enabled: true
  security:
    max_login_attempts: 5
    lockout_duration_seconds: 900
    password_change_cooldown_seconds: 86400
  registration:
    enabled: true
    require_email_verification: false
    default_role: "user"
```

### Password Policy

- **min_length**: Minimum password length. Longer is more secure but harder for users.
- **max_length**: Maximum password length.
- **require_uppercase**: Require at least one uppercase letter (A-Z).
- **require_lowercase**: Require at least one lowercase letter (a-z).
- **require_digit**: Require at least one digit (0-9).
- **require_special**: Require at least one special character (!@#$%^&* etc).

### Account Settings

- **username_min_length**: Minimum username length.
- **username_max_length**: Maximum username length.
- **username_pattern**: Regex pattern for valid usernames. Default allows letters, numbers, underscores only.
- **email_validation.strict**: Enable strict TLD validation (reject emails with invalid or unknown TLDs).
- **email_validation.allow_custom_tlds**: Allow TLDs not in the built-in list (useful for internal networks or new TLDs).
- **email_validation.valid_tlds**: Custom list of allowed TLDs. If empty, uses comprehensive built-in list (200+ TLDs).

**Reserved Usernames:** admin, administrator, system, bot, api, root, null, undefined (always reserved)

### Session Management

- **max_sessions_per_user**: Maximum concurrent sessions per user. Oldest session is invalidated when exceeded.
- **session_lifetime_seconds**: How long access tokens remain valid (2592000 = 30 days).
- **refresh_token_lifetime_seconds**: How long refresh tokens remain valid (7776000 = 90 days).
- **device_tracking_enabled**: Track device information (user agent, IP) for security auditing.

### Two-Factor Authentication (TOTP)

- **enabled**: Enable TOTP 2FA using authenticator apps (Google Authenticator, Authy, etc).
- **issuer**: Issuer name shown in authenticator apps (typically your service name).
- **digits**: Number of digits in TOTP codes (standard is 6).
- **interval**: Time step in seconds for code generation (standard is 30).
- **backup_code_count**: Number of one-time backup codes generated when 2FA is enabled.
- **backup_code_length**: Length of each backup code in characters.
- **backup_code_max_checks**: Maximum backup code checks allowed per request (DoS protection).

### Account Deletion

- **enabled**: Enable user-initiated account deletion.
- **grace_period_days**: Number of days before permanent deletion after user requests deletion (allows cancellation).
- **anonymize_content**: If true, messages are anonymized to "[This message was sent by a deleted user]". If false, messages are deleted entirely.
- **audit_log.enabled**: Enable cryptographically chained audit log for GDPR compliance (tamper-evident).
- **audit_log.file_path**: Path to the audit log file (JSONL format, one JSON object per line).
- **audit_log.hash_chain_enabled**: Enable hash chaining where each entry contains the previous entry's hash (detects tampering).
- **reaper.interval_hours**: How often the background task runs to purge expired accounts.
- **reaper.batch_size**: Maximum accounts to purge per reaper cycle (prevents long-running operations).
- **reaper.boot_check_enabled**: Perform rollback protection on startup (detects accounts that should be deleted if database was restored).

### Security

- **max_login_attempts**: Maximum failed login attempts before account lockout.
- **lockout_duration_seconds**: How long account remains locked after exceeding max attempts (900 = 15 minutes).
- **password_change_cooldown_seconds**: Minimum time between password changes (prevents rapid cycling, 86400 = 24 hours).

### Registration

- **enabled**: Enable new user registration.
- **require_email_verification**: Require email verification before account activation (requires email configuration).
- **default_role**: Default role assigned to new users.

---

## API Settings

REST API configuration.

```yaml
api:
  prefix: "/api/v1"
  docs_url: "/docs"
  redoc_url: "/redoc"
  openapi_url: "/openapi.json"
  cors:
    enabled: true
    allow_origins: ["*"]
    allow_methods: ["*"]
    allow_headers: ["*"]
    allow_credentials: false
    max_age: 600
  max_request_body_size: 10485760
  debug: false
```

- **prefix**: URL prefix for all API endpoints.
- **docs_url**: URL path for Swagger UI documentation.
- **redoc_url**: URL path for ReDoc documentation.
- **openapi_url**: URL path for OpenAPI schema JSON.
- **cors.enabled**: Enable CORS (Cross-Origin Resource Sharing).
- **cors.allow_origins**: List of allowed origins. `["*"]` allows all (not recommended for production).
- **cors.allow_methods**: List of allowed HTTP methods. `["*"]` allows all.
- **cors.allow_headers**: List of allowed headers. `["*"]` allows all.
- **cors.allow_credentials**: Allow cookies and authentication headers in CORS requests.
- **cors.max_age**: How long CORS preflight requests are cached in seconds (600 = 10 minutes).
- **max_request_body_size**: Maximum request body size in bytes (10485760 = 10MB).
- **debug**: Enable debug mode (verbose error responses, never use in production).

---

## WebSocket Settings

WebSocket gateway configuration for real-time communication.

```yaml
websocket:
  enabled: true
  path: "/gateway"
  max_connections: 10000
  heartbeat_interval_seconds: 30
  heartbeat_timeout_seconds: 60
  max_message_size: 1048576
  max_decompressed_size: 10485760
  compression:
    enabled: true
    level: 6
  rate_limit:
    enabled: true
    connections_per_ip: 5
    messages_per_second: 50
```

- **enabled**: Enable WebSocket gateway.
- **path**: URL path for WebSocket endpoint.
- **max_connections**: Maximum concurrent WebSocket connections.
- **heartbeat_interval_seconds**: How often server sends heartbeat pings (30 = every 30 seconds).
- **heartbeat_timeout_seconds**: How long without heartbeat before disconnect (60 = 1 minute).
- **max_message_size**: Maximum message size in bytes before compression (1048576 = 1MB).
- **max_decompressed_size**: Maximum message size after decompression (10485760 = 10MB). Prevents zip bomb attacks.
- **compression.enabled**: Enable zlib-stream compression for messages.
- **compression.level**: Compression level (0-9, where 9 is maximum compression but slower).
- **rate_limit.enabled**: Enable WebSocket rate limiting.
- **rate_limit.connections_per_ip**: Maximum concurrent connections per IP address.
- **rate_limit.messages_per_second**: Maximum messages per second per connection.

---

## Messaging Settings

Message and conversation configuration.

```yaml
messaging:
  max_message_length: 2000
  max_attachments_per_message: 10
  max_embeds_per_message: 10
  max_mentions_per_message: 20
  edit_time_limit_seconds: 600
  delete_time_limit_seconds: 3600
  threads:
    enabled: true
    max_thread_depth: 3
  reactions:
    enabled: true
    max_reactions_per_message: 20
    max_custom_reactions_per_message: 10
```

- **max_message_length**: Maximum characters per message.
- **max_attachments_per_message**: Maximum file attachments per message.
- **max_embeds_per_message**: Maximum rich embeds per message.
- **max_mentions_per_message**: Maximum @mentions per message (prevents mention spam).
- **edit_time_limit_seconds**: Time window for editing messages after sending (600 = 10 minutes).
- **delete_time_limit_seconds**: Time window for deleting messages after sending (3600 = 1 hour).
- **threads.enabled**: Enable message threads (replies to specific messages).
- **threads.max_thread_depth**: Maximum nesting level of threads.
- **reactions.enabled**: Enable emoji reactions on messages.
- **reactions.max_reactions_per_message**: Maximum unique reactions per message.
- **reactions.max_custom_reactions_per_message**: Maximum custom emoji reactions per message.

---

## Media Settings

File upload, storage, and processing configuration. For detailed guidance on storage backends, security, and CDN integration, see [Media Configuration](config-media.md).

```yaml
media:
  storage_backend: "local"
  local:
    path: "data/media"
    url_prefix: "/media"
  s3:
    bucket: "${S3_BUCKET}"
    region: "${S3_REGION:-us-east-1}"
    access_key_id: "${S3_ACCESS_KEY}"
    secret_access_key: "${S3_SECRET_KEY}"
    endpoint_url: "${S3_ENDPOINT}"
    public_url: "${S3_PUBLIC_URL}"
  max_file_size: 104857600
  max_total_size_per_user: 10737418240
  allowed_types:
    images: ["image/jpeg", "image/png", "image/gif", "image/webp"]
    videos: ["video/mp4", "video/webm"]
    audio: ["audio/mpeg", "audio/ogg", "audio/wav"]
    documents: ["application/pdf", "text/plain"]
  processing:
    resize_images: true
    max_image_width: 4096
    max_image_height: 4096
    thumbnail_size: 300
    compress_videos: false
  malware_scanning:
    enabled: false
    clamav_socket: "/var/run/clamav/clamd.ctl"
  rate_limit:
    uploads_per_minute: 10
    thumbnails_per_minute: 30
  external_proxy:
    enabled: false
    timeout_seconds: 10
```

### Storage Backend

- **storage_backend**: Storage engine. Options: `local` (filesystem), `s3` (AWS S3 or compatible), `database` (blob storage in DB).

### Local Storage

- **path**: Directory path for storing uploaded files.
- **url_prefix**: URL prefix for serving local files.

### S3 Storage

- **bucket**: S3 bucket name.
- **region**: AWS region.
- **access_key_id**: AWS access key ID.
- **secret_access_key**: AWS secret access key (keep secure).
- **endpoint_url**: Custom S3 endpoint URL (for MinIO, Wasabi, etc).
- **public_url**: Base URL for public access to files (if using CloudFront or CDN).

### File Limits

- **max_file_size**: Maximum file size in bytes (104857600 = 100MB).
- **max_total_size_per_user**: Maximum total storage per user in bytes (10737418240 = 10GB).

### Allowed Types

- **allowed_types.images**: MIME types for image uploads.
- **allowed_types.videos**: MIME types for video uploads.
- **allowed_types.audio**: MIME types for audio uploads.
- **allowed_types.documents**: MIME types for document uploads.

### Processing

- **processing.resize_images**: Automatically resize large images.
- **processing.max_image_width**: Maximum image width in pixels.
- **processing.max_image_height**: Maximum image height in pixels.
- **processing.thumbnail_size**: Thumbnail size in pixels (square).
- **processing.compress_videos**: Enable video compression (resource-intensive).

### Malware Scanning

- **malware_scanning.enabled**: Enable ClamAV virus scanning.
- **malware_scanning.clamav_socket**: Path to ClamAV socket file.

### Rate Limiting

- **rate_limit.uploads_per_minute**: Maximum file uploads per minute per user.
- **rate_limit.thumbnails_per_minute**: Maximum thumbnail generations per minute per user.

### External Proxy

- **external_proxy.enabled**: Enable proxying external URLs through your server (prevents IP leakage).
- **external_proxy.timeout_seconds**: Timeout for fetching external URLs.

---

## Rate Limiting Settings

API rate limiting configuration.

```yaml
rate_limiting:
  enabled: true
  global:
    requests: 100
    window_seconds: 60
    burst: 50
  user:
    requests: 50
    window_seconds: 60
    burst: 25
  ip:
    requests: 30
    window_seconds: 60
    burst: 15
  route:
    "/api/v1/auth/login":
      requests: 5
      window_seconds: 60
      burst: 2
  bot_multiplier: 0.5
  webhook_multiplier: 2.0
  bypass_internal: true
  bypass_admin: true
```

- **enabled**: Enable rate limiting globally.
- **global.requests**: Global request limit across all users.
- **global.window_seconds**: Time window for global limit (60 = 1 minute).
- **global.burst**: Burst allowance (allows temporary spikes).
- **user.requests**: Request limit per user.
- **user.window_seconds**: Time window for user limit.
- **user.burst**: Burst allowance per user.
- **ip.requests**: Request limit per IP address.
- **ip.window_seconds**: Time window for IP limit.
- **ip.burst**: Burst allowance per IP.
- **route**: Per-route rate limits (nested by route path).
- **bot_multiplier**: Multiplier for bot accounts (0.5 = bots get half the limit of users).
- **webhook_multiplier**: Multiplier for webhook requests (2.0 = webhooks get 2x the limit).
- **bypass_internal**: Bypass rate limiting for internal requests.
- **bypass_admin**: Bypass rate limiting for admin users.

---

## Servers Settings

Server/guild configuration.

```yaml
servers:
  max_name_length: 100
  max_members: 250000
  max_channels_per_server: 500
  max_roles_per_server: 250
  onboarding:
    max_onboarding_steps: 10
    max_welcome_channels: 5
    max_step_options: 25
  templates:
    max_templates_per_user: 25
    template_code_length: 8
    max_channels_in_template: 100
    max_roles_in_template: 50
  events:
    enabled: true
    retention_days: 90
```

- **max_name_length**: Maximum server name length.
- **max_members**: Maximum members per server.
- **max_channels_per_server**: Maximum channels per server.
- **max_roles_per_server**: Maximum roles per server.
- **onboarding.max_onboarding_steps**: Maximum steps in server onboarding flow.
- **onboarding.max_welcome_channels**: Maximum channels shown in welcome screen.
- **onboarding.max_step_options**: Maximum options per onboarding step.
- **templates.max_templates_per_user**: Maximum server templates per user.
- **templates.template_code_length**: Length of template share codes.
- **templates.max_channels_in_template**: Maximum channels saved in a template.
- **templates.max_roles_in_template**: Maximum roles saved in a template.
- **events.enabled**: Enable server event logging.
- **events.retention_days**: How long to retain event logs (90 = 90 days).

---

## User Features Settings

User tier and feature flag configuration.

```yaml
user_features:
  alpha_registration_enabled: false
  rate_limit_tiers:
    standard:
      multiplier: 1.0
      max_voice_minutes_per_day: 120
      max_video_minutes_per_day: 60
      max_file_uploads_per_day: 50
      max_file_size_mb: 10
      max_servers: 100
    alpha:
      multiplier: 2.0
      max_voice_minutes_per_day: 480
      max_video_minutes_per_day: 240
      max_file_uploads_per_day: 200
      max_file_size_mb: 25
      max_servers: 200
    premium:
      multiplier: 3.0
      max_voice_minutes_per_day: -1
      max_video_minutes_per_day: -1
      max_file_uploads_per_day: 500
      max_file_size_mb: 100
      max_servers: 500
  default_tier: "standard"
  badge_display_limit: 5
  available_badges:
    - alpha_tester
    - early_supporter
    - staff
    - verified
    - bug_hunter
    - contributor
```

- **alpha_registration_enabled**: Automatically grant alpha tier to new registrations (for testing).
- **rate_limit_tiers**: Define tier-specific limits. Use `-1` for unlimited.
  - **multiplier**: Rate limit multiplier (higher = more lenient limits).
  - **max_voice_minutes_per_day**: Daily voice usage limit.
  - **max_video_minutes_per_day**: Daily video usage limit.
  - **max_file_uploads_per_day**: Daily file upload limit.
  - **max_file_size_mb**: Maximum file size in MB.
  - **max_servers**: Maximum server membership.
- **default_tier**: Default tier for new users.
- **badge_display_limit**: Maximum badges shown on user profile.
- **available_badges**: List of badges that can be assigned to users.

---

## Voice Settings

WebRTC voice/video configuration. For detailed guidance on SFU backends, STUN/TURN servers, and NAT traversal, see [Voice Configuration](config-voice.md).

```yaml
voice:
  enabled: true
  sfu_backend: "aiortc"
  mediasoup_url: "wss://localhost:4443"
  mediasoup_origin: "https://localhost"
  janus_url: "http://localhost:8088/janus"
  stun_urls:
    - "stun:stun.l.google.com:19302"
  turn_urls: []
  turn_secret: ""
  turn_ttl: 86400
  turn_username: ""
  turn_credential: ""
  max_bitrate: 128000
  max_participants_per_channel: 25
```

- **enabled**: Enable voice/video features.
- **sfu_backend**: Selective Forwarding Unit backend. Options: `aiortc` (in-process, Python-only), `mediasoup-ws` (WebSocket to mediasoup), `mediasoup` (REST API to mediasoup), `janus` (Janus Gateway).
- **mediasoup_url**: Mediasoup server URL (WebSocket or REST depending on backend).
- **mediasoup_origin**: Origin header for CORS when using mediasoup-ws.
- **janus_url**: Janus Gateway API URL.
- **stun_urls**: List of STUN server URLs for NAT traversal (free public STUN servers available).
- **turn_urls**: List of TURN server URLs for relay through NAT (requires authentication).
- **turn_secret**: Shared secret for time-limited TURN credentials (coturn).
- **turn_ttl**: TURN credential TTL in seconds (86400 = 24 hours).
- **turn_username**: Static TURN username (for services like metered.ca).
- **turn_credential**: Static TURN password (for services like metered.ca).
- **max_bitrate**: Maximum audio/video bitrate in bits per second (128000 = 128 kbps).
- **max_participants_per_channel**: Maximum participants in a voice channel.

---

## Search Settings

Full-text search configuration.

```yaml
search:
  enabled: true
  backend: "sqlite"
  elasticsearch:
    hosts: ["http://localhost:9200"]
    index_prefix: "plexichat"
  meilisearch:
    host: "http://localhost:7700"
    index_prefix: "plexichat"
  batch_size: 100
  write_time_indexing: true
  result_limit: 100
  min_query_length: 2
  max_query_length: 200
  discovery:
    enabled: true
    min_members: 50
    cooldown_hours: 24
```

- **enabled**: Enable search functionality.
- **backend**: Search engine. Options: `sqlite` (built-in FTS5), `elasticsearch`, `meilisearch`, `postgres` (PostgreSQL FTS).
- **elasticsearch.hosts**: List of Elasticsearch node URLs.
- **elasticsearch.index_prefix**: Prefix for Elasticsearch indices.
- **meilisearch.host**: Meilisearch server URL.
- **meilisearch.index_prefix**: Prefix for Meilisearch indices.
- **batch_size**: Number of items to index in a single batch.
- **write_time_indexing**: Index content immediately when created (can impact write performance).
- **result_limit**: Maximum search results returned.
- **min_query_length**: Minimum search query length.
- **max_query_length**: Maximum search query length.
- **discovery.enabled**: Enable server discovery (public server listing).
- **discovery.min_members**: Minimum member count for server to be discoverable.
- **discovery.cooldown_hours**: Cooldown between search requests for server discovery.

---

## OAuth Settings

OAuth2 authentication configuration.

```yaml
oauth:
  authorization_endpoint: "/oauth2/authorize"
  token_endpoint: "/oauth2/token"
  scopes:
    - "identify"
    - "email"
    - "guilds"
    - "bot"
  token_expiry_seconds: 3600
  code_expiry_seconds: 600
  refresh_enabled: true
```

- **authorization_endpoint**: OAuth2 authorization URL path.
- **token_endpoint**: OAuth2 token exchange URL path.
- **scopes**: List of available OAuth2 scopes.
- **token_expiry_seconds**: OAuth2 access token lifetime (3600 = 1 hour).
- **code_expiry_seconds**: OAuth2 authorization code lifetime (600 = 10 minutes).
- **refresh_enabled**: Enable OAuth2 refresh tokens.

---

## Encryption Settings

Cryptographic configuration.

```yaml
encryption:
  algorithm: "argon2id"
  argon2:
    time_cost: 3
    memory_cost: 65536
    parallelism: 4
    salt_length: 16
    hash_length: 32
```

- **algorithm**: Password hashing algorithm. Currently only `argon2id` is supported.
- **argon2.time_cost**: Number of iterations (higher = slower but more secure).
- **argon2.memory_cost**: Memory usage in KiB (65536 = 64MB).
- **argon2.parallelism**: Number of threads (should match CPU cores).
- **argon2.salt_length**: Salt length in bytes.
- **argon2.hash_length**: Hash length in bytes.

---

## Monitoring Settings

Application monitoring and metrics.

```yaml
monitoring:
  enabled: false
  prometheus:
    enabled: false
    port: 9090
    path: "/metrics"
  health_check:
    enabled: true
    path: "/health"
  metrics:
    enabled: true
    collect_runtime: true
    collect_database: true
    collect_redis: true
```

- **enabled**: Enable monitoring features.
- **prometheus.enabled**: Enable Prometheus metrics endpoint.
- **prometheus.port**: Port for Prometheus metrics server.
- **prometheus.path**: URL path for Prometheus metrics.
- **health_check.enabled**: Enable health check endpoint.
- **health_check.path**: URL path for health check.
- **metrics.enabled**: Enable internal metrics collection.
- **metrics.collect_runtime**: Collect runtime metrics (CPU, memory, GC).
- **metrics.collect_database**: Collect database metrics.
- **metrics.collect_redis**: Collect Redis metrics.

---

## TLS Settings

TLS/SSL certificate configuration.

```yaml
tls:
  cert_path: ""
  key_path: ""
  cert_days: 365
  auto_generate: true
```

- **cert_path**: Path to TLS certificate file. If empty, auto-generates self-signed cert.
- **key_path**: Path to TLS private key file. If empty, auto-generates self-signed key.
- **cert_days**: Validity period for auto-generated certificates in days (365 = 1 year).
- **auto_generate**: Automatically generate self-signed certificates if files don't exist (development only).

**Security Warning:** Self-signed certificates are suitable for development only. Use certificates from a trusted CA (Let's Encrypt, etc.) for production.

---

## Email Settings

Email notification configuration.

```yaml
email:
  enabled: false
  smtp:
    host: "localhost"
    port: 587
    use_tls: true
    username: ""
    password: ""
    from_address: "noreply@plexichat.example"
    from_name: "Plexichat"
```

- **enabled**: Enable email notifications.
- **smtp.host**: SMTP server hostname.
- **smtp.port**: SMTP server port (587 for submission, 465 for SMTPS, 25 for SMTP).
- **smtp.use_tls**: Enable STARTTLS for connection security.
- **smtp.username**: SMTP authentication username.
- **smtp.password**: SMTP authentication password (keep secure).
- **smtp.from_address**: Default sender email address.
- **smtp.from_name**: Default sender name.

---

## Presence Settings

User presence and activity configuration.

```yaml
presence:
  timeout_ms: 300000
  typing_timeout_ms: 6000
  custom_status_max_length: 128
  custom_status_emoji_enabled: true
```

- **timeout_ms**: Milliseconds of inactivity before user is marked offline (300000 = 5 minutes).
- **typing_timeout_ms**: Milliseconds before typing indicator expires (6000 = 6 seconds).
- **custom_status_max_length**: Maximum custom status length.
- **custom_status_emoji_enabled**: Allow emoji in custom status.

---

## Polls Settings

Poll configuration.

```yaml
polls:
  enabled: true
  min_options: 2
  max_options: 10
  min_duration_hours: 1
  max_duration_hours: 168
  max_question_length: 300
  max_option_length: 100
```

- **enabled**: Enable poll feature.
- **min_options**: Minimum poll options required.
- **max_options**: Maximum poll options allowed.
- **min_duration_hours**: Minimum poll duration in hours.
- **max_duration_hours**: Maximum poll duration in hours (168 = 7 days).
- **max_question_length**: Maximum poll question length.
- **max_option_length**: Maximum poll option length.

---

## Emojis Settings

Custom emoji configuration.

```yaml
emojis:
  enabled: true
  max_emojis_per_server: 50
  max_emoji_name_length: 32
  max_emoji_size_kb: 256
  animated_enabled: true
```

- **enabled**: Enable custom emoji feature.
- **max_emojis_per_server**: Maximum custom emojis per server.
- **max_emoji_name_length**: Maximum emoji name length.
- **max_emoji_size_kb**: Maximum emoji file size in KB.
- **animated_enabled**: Allow animated GIF emojis.

---

## Stickers Settings

Sticker configuration.

```yaml
stickers:
  enabled: true
  max_packs_per_server: 10
  max_stickers_per_pack: 50
  max_pack_name_length: 50
  max_sticker_name_length: 30
  allowed_formats:
    - "png"
    - "apng"
    - "json"
  max_sticker_size_kb: 512
```

- **enabled**: Enable sticker feature.
- **max_packs_per_server**: Maximum sticker packs per server.
- **max_stickers_per_pack**: Maximum stickers per pack.
- **max_pack_name_length**: Maximum pack name length.
- **max_sticker_name_length**: Maximum sticker name length.
- **allowed_formats**: Allowed sticker formats (PNG, APNG, Lottie JSON).
- **max_sticker_size_kb**: Maximum sticker file size in KB.

---

## Soundboard Settings

Soundboard configuration.

```yaml
soundboard:
  enabled: true
  max_sounds_per_server: 50
  max_sound_name_length: 30
  max_sound_size_mb: 2
  max_sound_duration_seconds: 10
  allowed_formats:
    - "mp3"
    - "ogg"
    - "wav"
  default_volume: 1.0
```

- **enabled**: Enable soundboard feature.
- **max_sounds_per_server**: Maximum sounds per server.
- **max_sound_name_length**: Maximum sound name length.
- **max_sound_size_mb**: Maximum sound file size in MB.
- **max_sound_duration_seconds**: Maximum sound duration in seconds.
- **allowed_formats**: Allowed audio formats.
- **default_volume**: Default playback volume (0.0 to 1.0).

---

## Webhooks Settings

Webhook configuration.

```yaml
webhooks:
  enabled: true
  max_webhooks_per_server: 10
  max_webhook_name_length: 80
  rate_limit_per_minute: 120
  retry_attempts: 3
  retry_delay_seconds: 5
  timeout_seconds: 10
```

- **enabled**: Enable webhook feature.
- **max_webhooks_per_server**: Maximum webhooks per server.
- **max_webhook_name_length**: Maximum webhook name length.
- **rate_limit_per_minute**: Maximum webhook deliveries per minute per server.
- **retry_attempts**: Number of retry attempts for failed deliveries.
- **retry_delay_seconds**: Delay between retry attempts.
- **timeout_seconds**: Timeout for webhook HTTP requests.

---

## AutoMod Settings

Automatic moderation configuration.

```yaml
automod:
  enabled: true
  default_actions:
    - "delete_message"
    - "alert_moderators"
  rate_limit_window: 60
  reputation_decay_rate: 1.0
  reputation_decay_interval: 86400
  max_violations_before_action: 1
  ai:
    openai:
      api_key: ""
      model: "gpt-4"
    perspective:
      api_key: ""
    custom:
      endpoint_url: ""
      api_key: ""
```

- **enabled**: Enable auto-moderation.
- **default_actions**: Default actions taken when rules are violated. Options: `delete_message`, `timeout_user`, `kick_user`, `ban_user`, `alert_moderators`.
- **rate_limit_window**: Time window for violation rate limiting in seconds.
- **reputation_decay_rate**: How fast user reputation decays per interval.
- **reputation_decay_interval**: Reputation decay interval in seconds (86400 = 24 hours).
- **max_violations_before_action**: Violations threshold before taking action.
- **ai.openai.api_key**: OpenAI API key for AI moderation (keep secure).
- **ai.openai.model**: OpenAI model to use.
- **ai.perspective.api_key**: Google Perspective API key (keep secure).
- **ai.custom.endpoint_url**: Custom AI moderation endpoint URL.
- **ai.custom.api_key**: API key for custom endpoint.

---

## Applications Settings

OAuth2 application/bot configuration.

```yaml
applications:
  max_applications_per_user: 100
  max_bot_accounts_per_user: 10
  bot_prefix_required: true
  token_expiry_seconds: 3600
  code_expiry_seconds: 600
  refresh_enabled: true
```

- **max_applications_per_user**: Maximum OAuth2 applications per user.
- **max_bot_accounts_per_user**: Maximum bot accounts per user.
- **bot_prefix_required**: Require bot names to end with "bot" (e.g., "MusicBot").
- **token_expiry_seconds**: Bot token lifetime (3600 = 1 hour).
- **code_expiry_seconds**: Authorization code lifetime (600 = 10 minutes).
- **refresh_enabled**: Enable bot token refresh.

---

## Admin UI Settings

Admin panel configuration.

```yaml
admin_ui:
  enabled: true
  path: "/admin"
  require_2fa: true
  session_timeout_minutes: 30
```

- **enabled**: Enable admin panel.
- **path**: URL path for admin panel.
- **require_2fa**: Require 2FA for admin access.
- **session_timeout_minutes**: Admin session timeout (30 = 30 minutes).

---

## Embeds Settings

Rich embed configuration.

```yaml
embeds:
  enabled: true
  max_embeds_per_message: 10
  max_title_length: 256
  max_description_length: 4096
  max_fields: 25
  max_field_name_length: 256
  max_field_value_length: 1024
  url_preview:
    enabled: true
    timeout_seconds: 8
    max_html_bytes: 524288
    max_redirects: 5
    max_image_size: 5242880
    cache_ttl_seconds: 3600
    rate_limit_requests: 10
    rate_limit_window_seconds: 60
    proxy_images: true
    allowed_schemes:
      - "http"
      - "https"
```

- **enabled**: Enable rich embeds.
- **max_embeds_per_message**: Maximum embeds per message.
- **max_title_length**: Maximum embed title length.
- **max_description_length**: Maximum embed description length.
- **max_fields**: Maximum fields per embed.
- **max_field_name_length**: Maximum field name length.
- **max_field_value_length**: Maximum field value length.

### URL Preview

- **url_preview.enabled**: Enable automatic link preview generation.
- **url_preview.timeout_seconds**: Timeout for fetching URL metadata.
- **url_preview.max_html_bytes**: Maximum HTML size to parse (524288 = 512KB).
- **url_preview.max_redirects**: Maximum redirect hops to follow.
- **url_preview.max_image_size**: Maximum preview image size (5242880 = 5MB).
- **url_preview.cache_ttl_seconds**: How long to cache previews (3600 = 1 hour).
- **url_preview.rate_limit_requests**: Maximum preview requests per user per window.
- **url_preview.rate_limit_window_seconds**: Rate limit window in seconds.
- **url_preview.proxy_images**: Proxy images through your server (prevents IP leakage).
- **url_preview.allowed_schemes**: Allowed URL schemes for previews.

---

## Telemetry Settings

Anonymous usage analytics configuration.

```yaml
telemetry:
  enabled: false
  endpoint: "https://telemetry.plexichat.example"
  include_version: true
  include_environment: false
  include_instance_id: true
```

- **enabled**: Enable anonymous telemetry collection.
- **endpoint**: Telemetry collection endpoint URL.
- **include_version**: Include version information in telemetry.
- **include_environment**: Include environment name in telemetry (avoid for sensitive deployments).
- **include_instance_id**: Include unique instance ID (for tracking unique deployments).

**Privacy Note:** Telemetry is anonymous by default. No user data or content is ever transmitted.

---

## Self-Test Settings

Self-diagnostic configuration.

```yaml
selftest:
  enabled: true
  run_on_startup: true
  database:
    enabled: true
  redis:
    enabled: true
  media:
    enabled: true
    test_upload: false
```

- **enabled**: Enable self-test system.
- **run_on_startup**: Run self-tests on application startup.
- **database.enabled**: Test database connectivity and basic operations.
- **redis.enabled**: Test Redis connectivity (if enabled).
- **media.enabled**: Test media storage backend.
- **media.test_upload**: Perform test upload during media check (creates then deletes a file).

---

## Docs Settings

Documentation configuration.

```yaml
docs:
  enabled: true
  path: "/docs"
```

- **enabled**: Enable interactive API documentation.
- **path**: URL path for documentation endpoint.

---

## Feedback Settings

User feedback configuration.

```yaml
feedback:
  enabled: true
  email: "feedback@plexichat.example"
  max_length: 5000
```

- **enabled**: Enable user feedback submission.
- **email**: Email address to receive feedback reports.
- **max_length**: Maximum feedback message length.

---

## Reports Settings

User report/configuration.

```yaml
reports:
  enabled: true
  max_reports_per_user: 10
  cooldown_hours: 24
```

- **enabled**: Enable user reporting system.
- **max_reports_per_user**: Maximum active reports per user.
- **cooldown_hours**: Cooldown between report submissions (24 = 24 hours).

---

## Avatars Settings

Avatar configuration.

```yaml
avatars:
  enabled: true
  max_size_kb: 1024
  default_colors:
    - "#5865F2"
    - "#57F287"
    - "#FEE75C"
    - "#EB459E"
    - "#ED4245"
    - "#9B59B6"
    - "#3498DB"
    - "#1ABC9C"
    - "#E67E22"
    - "#95A5A6"
```

- **enabled**: Enable user avatars.
- **max_size_kb**: Maximum avatar file size in KB (1024 = 1MB).
- **default_colors**: List of default avatar background colors (for users without custom avatars).

---

## Related Documentation

- [Main Configuration Guide](configuration.md)
- [Authentication Configuration](config-authentication.md)
- [Database Configuration](config-database.md)
- [Deployment Guide](deployment.md)
- [Redis Configuration](config-redis.md)
- [Media Configuration](config-media.md)
- [Voice Configuration](config-voice.md)
