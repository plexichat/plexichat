# Default Configuration Reference

This document provides a comprehensive reference for all configuration options in Plexichat. Each setting shows its actual default value as used by the application when no configuration file is present. Values are taken directly from the built-in defaults.

## Complete Default Configuration

Copy this complete configuration file as your starting point. Save it as `config/config.yaml` in your project directory or `~/.plexichat/config/config.yaml` in your home directory, then modify the values according to your deployment requirements.

**Important:** The key names below are the exact keys the application reads. Do not rename keys or the application will not recognize them. For detailed guidance on what each section controls, see the linked configuration guides.

```yaml
# Plexichat Default Configuration
# Copy this file to config/config.yaml or ~/.plexichat/config/config.yaml
# Modify values according to your deployment needs

application:
  name: "Plexichat"
  version: "a.1.0-51"
  environment: "development"  # "development", "staging", or "production"

server:
  host: "127.0.0.1"
  port: 8000
  workers: 1
  reload: false

logging:
  level: "DEBUG"  # "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"
  max_bytes: 10485760  # 10MB per log file before rotation
  backup_count: 5  # number of rotated log files to keep
  zip_logs: true  # compress rotated log files
  rotate: true  # enable log rotation
  include_exception_details: false  # SECURITY: set to false in production

database:
  type: "sqlite"  # "sqlite" or "postgres"
  path: "~/.plexichat/data/plexichat.db"  # SQLite path (uses home directory)
  postgres:
    host: "${POSTGRES_HOST:-localhost}"
    port: 5432
    user: "${POSTGRES_USER:-postgres}"
    password: "${POSTGRES_PASSWORD:-}"  # Optional, empty default for local dev
    dbname: "${POSTGRES_DBNAME:-plexichat}"
    sslmode: "${POSTGRES_SSLMODE:-prefer}"
  connection_pool:
    min_connections: 5
    max_connections: 100
    connect_timeout: 10
  monitoring:
    slow_query_threshold_ms: 1000
    alert_on_slow_queries: true
  migrations:
    auto_migrate: true
    migration_dir: "~/.plexichat/migrations"

redis:
  enabled: false
  host: "${REDIS_HOST:-localhost}"
  port: 6379
  password: "${REDIS_PASSWORD:-}"  # Optional, empty default for local dev
  db: 0
  ssl: false
  key_prefix: "plexichat:"
  connection_pool:
    max_connections: 50
    timeout: 5
  ttl:
    session: 1800  # 30 minutes
    presence: 300  # 5 minutes
    cache: 60  # 1 minute
  cache_max_items: 1000

authentication:
  encryption:
    require_secure_source: true
    media_key: "${PLEXICHAT_MEDIA_KEY:-}"  # Optional, derived from signing key if not set
  accounts:
    allow_registration: true  # not "registration.enabled"
    require_email_verification: false
    max_bots_per_user: 5
    username_min_length: 3
    username_max_length: 32
    age_gate_enabled: false
    minimum_age: 13
    age_verification_type: "boolean"  # "boolean" or "dob"
  email_validation:
    strict: true
    allow_custom_tlds: false
    valid_tlds: []
  sessions:
    token_bytes: 32
    expire_hours: 168  # 7 days (not "session_lifetime_seconds")
    max_per_user: 10  # (not "max_sessions_per_user")
    extend_on_activity: true
    extend_threshold_hours: 24
  security:
    max_failed_attempts: 5  # (not "max_login_attempts")
    lockout_duration_minutes: 15  # (not "lockout_duration_seconds")
    token_cache_ttl: 30
    token_verify_rate_limit: 100
    token_binding: false
  totp:
    issuer: "Plexichat"
    digits: 6
    interval: 30
    backup_code_count: 10
  password:
    min_length: 12
    max_length: 128
    require_uppercase: true
    require_lowercase: true
    require_digit: true
    require_special: true
  bots:
    token_bytes: 48
    require_owner_2fa: false
  account_deletion:
    enabled: true
    grace_period_days: 30
    reminder_days_before_purge: [7, 1]
    hard_freeze: true
    anonymize_content: true
    audit_log:
      file_path: "~/.plexichat/audit/deletion_log.jsonl"
      hash_chain_enabled: true
      backup_to_s3: true
      s3_backup_path: "audit/deletions/log_backup.jsonl"
    reaper:
      interval_hours: 24
      boot_check_enabled: true
      batch_size: 50
  dsar:
    enabled: true
    require_admin_review: true
    default_format: "json"
    export_formats: ["json", "zip"]
    max_export_size_mb: 500
    retention_days: 7
    pending_expiry_days: 30
    # Local export directory when media.storage_backend is "local".
    # The backend itself, S3 credentials, and database settings are all
    # inherited from the `media` block - DSAR reuses the same storage.
    local_path: "~/.plexichat/data/exports/dsar"
    s3_path_prefix: "dsar-exports"
    audit_log:
      file_path: "~/.plexichat/data/dsar_audit_log.jsonl"
      hash_chain_enabled: true
      backup_to_s3: true
      s3_backup_path: "audit/dsar/log_backup.jsonl"
      halt_on_invalid_audit: true
    harvester:
      interval_hours: 24
      boot_check_enabled: true
      batch_size: 20

api:
  title: "Plexichat API"
  description: "REST API for the Plexichat messaging platform"
  api_prefix: "/api/v1"
  debug: true  # set to false in production
  cors_origins:
    - "http://localhost:5000"
    - "http://127.0.0.1:5000"
    - "http://localhost:8000"
    - "http://127.0.0.1:8000"
    - "https://plexichat.com"
    - "https://app.plexichat.com"
    - "https://api.plexichat.com"
    - "http://localhost:8443"
  cors_allow_credentials: true
  cors_allow_methods: ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
  cors_allow_headers: ["Authorization", "Content-Type", "X-Requested-With", "Accept", "Origin"]
  trusted_proxies: []
  trust_x_forwarded_for: false
  docs_url: "/docs"
  redoc_url: "/redoc"
  openapi_url: "/openapi.json"

websocket:
  heartbeat_interval_ms: 45000  # 45 seconds (not "heartbeat_interval_seconds")
  session_timeout_ms: 60000  # 60 seconds (not "heartbeat_timeout_seconds")
  max_connections_per_user: 5
  rate_limit_per_minute: 120
  max_message_size: 65536  # 64KB (not 1MB)
  max_decompressed_size: 262144  # 256KB (not 10MB)
  compression_enabled: true
  allowed_origins: []

messaging:
  encrypt_messages: true
  encrypt_attachments: true
  max_message_length: 4000  # (not 2000)
  max_group_participants: 100
  max_attachment_size: 10485760  # 10MB
  max_attachments_per_message: 10
  dm_auto_create: true
  message_preview_length: 100

media:
  data_dir: "~/.plexichat/data"
  logs_dir: "~/.plexichat/logs"
  media_dir: "~/.plexichat/media"
  temp_dir: "~/.plexichat/temp"
  storage_backend: "local"  # "local", "s3", or "database"
  encrypt_at_rest: true
  local_path: "~/.plexichat/media"
  local_url: "/media"
  s3_bucket: "${S3_BUCKET:-}"  # Optional, required only if storage_backend is s3
  s3_access_key: "${S3_ACCESS_KEY:-}"
  s3_secret_key: "${S3_SECRET_KEY:-}"
  s3_region: "${S3_REGION:-us-east-1}"
  s3_endpoint: "${S3_ENDPOINT:-}"
  s3_public_url: "${S3_PUBLIC_URL:-}"
  database_url: "/api/v1/media/blob"
  database_max_size: 524288  # 512KB
  auto_route_to_database:
    enabled: true
    max_size: 524288
    content_types: ["text/plain", "application/json", "text/markdown", "text/csv"]
  size_limits:
    image: 10485760  # 10MB
    video: 104857600  # 100MB
    audio: 52428800  # 50MB
    document: 26214400  # 25MB
    icon: 2097152  # 2MB
    avatar: 5242880  # 5MB
    other: 10485760  # 10MB
  allowed_types:
    image: ["image/jpeg", "image/png", "image/gif", "image/webp"]
    video: ["video/mp4", "video/webm", "video/quicktime"]
    audio: ["audio/mpeg", "audio/ogg", "audio/wav", "audio/webm"]
    document: ["application/pdf", "text/plain", "application/zip", "text/markdown", "application/json"]
  thumbnail_sizes: [64, 128, 256, 512]
  image_quality: 85
  image_optimize: true
  image_processing:
    max_dimension: 16384
    max_pixels: 178956970
    max_thumbnail_requests_per_minute: 60
  video_processing:
    ffprobe_timeout: 30
    max_size_for_metadata: 524288000
  signing_key: "CHANGE_THIS_SIGNING_KEY"
  signing_expiry: 3600
  scanner_enabled: false
  scanner_host: "localhost"
  scanner_port: 3310
  proxy_enabled: true
  proxy_cache_ttl: 86400
  proxy_max_size: 10485760
  proxy_buffer_size: 65536
  rate_limit:
    enabled: true
    uploads_per_minute: 10
    uploads_per_hour: 100
    max_total_size_per_day: 536870912  # 512MB
  phash:
    enabled: true
    algorithm: "phash"
    hash_size: 8
    similarity_threshold: 10
    highfreq_factor: 4
  deduplication:
    enabled: true
    hash_algorithm: "sha256"
    min_size: 10240
    auto_block_threshold: 5

rate_limiting:
  enabled: true
  global:
    requests: 100
    window_seconds: 60.0
    burst: 50
  user:
    requests: 120  # (not 50)
    window_seconds: 60.0
    burst: 20
    hourly_limit: 3600
    daily_limit: 50000
  ip:
    requests: 60
    window_seconds: 60.0
    burst: 10
    hourly_limit: 1800
    daily_limit: 10000
  bot_multiplier: 1.5  # (not 0.5)
  webhook_multiplier: 1.0  # (not 2.0)
  admin_bypass: true
  internal_bypass: true
  bypass_secret: "<auto-generated-hex>"

servers:
  server_name_min_length: 2
  server_name_max_length: 100
  channel_name_max_length: 100
  role_name_max_length: 100
  invite_code_length: 12
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

user_features:
  alpha_registration_enabled: true  # (not false)
  default_tier: "standard"
  badge_display_limit: 5
  available_badges:
    - alpha_tester
    - early_supporter
    - staff
    - verified
    - bug_hunter
    - contributor
    - moderator
    - partner
  rate_limit_tiers:
    standard:
      multiplier: 1.0
      max_voice_minutes_per_day: 120
      max_video_minutes_per_day: 60
      max_file_uploads_per_day: 50
      max_file_size_mb: 50  # (not 10)
      max_servers: 100
      max_message_length: 2000
      max_reactions_per_message: 20
      max_pins_per_channel: 50
      custom_emoji_slots: 50
    alpha:
      multiplier: 2.0
      max_voice_minutes_per_day: 480
      max_video_minutes_per_day: 240
      max_file_uploads_per_day: 200
      max_file_size_mb: 25
      max_servers: 200
      max_message_length: 4000
      max_reactions_per_message: 50
      max_pins_per_channel: 100
      custom_emoji_slots: 100
    premium:
      multiplier: 3.0
      max_voice_minutes_per_day: -1
      max_video_minutes_per_day: -1
      max_file_uploads_per_day: 500
      max_file_size_mb: 100
      max_servers: 500
      max_message_length: 4000
      max_reactions_per_message: 100
      max_pins_per_channel: 200
      custom_emoji_slots: 250
    staff:
      multiplier: 10.0
      max_voice_minutes_per_day: -1
      max_video_minutes_per_day: -1
      max_file_uploads_per_day: -1
      max_file_size_mb: 500
      max_servers: -1
      max_message_length: 8000
      max_reactions_per_message: -1
      max_pins_per_channel: -1
      custom_emoji_slots: -1
  admin_rate_limit:
    max_per_minute: 30
    max_per_hour: 200

voice:
  enabled: true
  sfu_backend: "mediasoup"  # (not "aiortc")
  mediasoup_url: "https://localhost:4443"
  janus_url: "http://localhost:8088/janus"
  stun_urls:
    - "stun:stun.l.google.com:19302"
    - "stun:stun1.l.google.com:19302"
    - "stun:stun2.l.google.com:19302"
    - "stun:stun3.l.google.com:19302"
  turn_urls: []
  turn_username: ""
  turn_credential: ""
  turn_secret: ""
  turn_ttl: 86400
  log_connections: false

search:
  enabled: true
  backend: "sqlite_fts5"  # (not "sqlite")
  result_limit: 100
  batch_size: 100
  write_time_indexing: true
  discovery:
    enabled: true
    min_members_for_listing: 10
    max_tags: 10
    bump_cooldown_hours: 4

oauth:
  state_ttl_seconds: 600
  state_token_bytes: 32
  nonce_token_bytes: 32
  cleanup_on_verify: true
  max_states_per_ip: 10
  pkce_enabled: true
  pkce:
    verifier_length: 64
    min_verifier_length: 43
    max_verifier_length: 128
  google:
    client_id: ""
    client_secret: ""
  github:
    client_id: ""
    client_secret: ""
  microsoft:
    client_id: ""
    client_secret: ""

encryption:
  argon2:
    time_cost: 2  # (not 3)
    memory_cost: 65536
    parallelism: 2  # (not 4)
    hash_length: 32
    salt_length: 16
  aes_gcm:
    key_length: 32
    nonce_length: 12
    tag_length: 16
  snowflake:
    epoch: "2024-01-01T00:00:00Z"
    worker_id: null
    datacenter_id: null
  key_rotation_days: 180

monitoring:
  enabled: true  # (not false)
  log_interval: 300
  metrics_enabled: true
  alert_thresholds:
    cpu_percent: 80
    memory_percent: 85
    db_pool_saturation_percent: 75
    query_time_ms: 5000
    db_errors_per_minute: 10
    api_response_time_ms: 2000
    error_rate_percent: 5
    active_connections: 1000

selftest:
  enabled: false
  run_on_startup: false
  exit_on_failure: false
  capture_stack_traces: true
  retry_on_failure: true
  excluded_endpoints: ["/api/v1/auth/logout", "/api/v1/admin/logout"]
  test_user:
    username: "selftest_admin"
    email: "selftest@internal.local"
    # Password is always auto-generated — never set it in config

admin_ui:
  enabled: true
  path: "/admin"
  require_otp: true  # (not "require_2fa")
  host_restriction:
    enabled: true
    allowed_hosts: ["127.0.0.1", "localhost", "::1"]
  blocked_ips: []
  allowed_origins: []
  rate_limit:
    max_attempts: 5
    window_seconds: 300
    lockout_seconds: 900

tls:
  enabled: false
  auto_generate_self_signed: false
  cert_path: "~/.plexichat/certs/server.crt"
  key_path: "~/.plexichat/certs/server.key"
  cert_days: 365

email:
  smtp_host: "localhost"
  smtp_port: 587
  smtp_user: ""
  from_email: "noreply@plexichat.internal"
  use_tls: true

presence:
  typing_timeout_ms: 10000
  timeout_ms: 300000
  update_interval_ms: 60000

polls:
  max_options: 10
  min_options: 2
  max_question_length: 300
  max_option_length: 100
  min_duration_hours: 1
  max_duration_hours: 168

emojis:
  max_emojis_per_server: 50
  max_animated_emojis_per_server: 50
  max_emoji_size: 262144  # 256KB in bytes
  emoji_min_name_length: 2
  emoji_max_name_length: 32
  allowed_formats: ["image/png", "image/jpeg", "image/gif", "image/webp"]

stickers:
  max_packs_per_server: 50
  max_stickers_per_pack: 100
  max_sticker_size: 524288  # 512KB in bytes
  max_sticker_name_length: 32
  max_pack_name_length: 64
  max_pack_description_length: 256
  allowed_formats: ["image/png", "image/webp", "image/gif"]
  max_suggestions: 10

soundboard:
  max_sounds_per_server: 50
  max_sound_size: 2097152  # 2MB in bytes
  max_sound_duration_seconds: 10
  max_sound_name_length: 64
  allowed_formats: ["audio/mpeg", "audio/ogg", "audio/wav", "audio/webm"]
  default_cooldown_seconds: 3.0
  max_cooldown_seconds: 30.0

webhooks:
  max_webhooks_per_channel: 100
  max_webhooks_per_server: 50
  max_message_length: 2000
  max_embeds_per_message: 10

automod:
  enabled: true
  exempt_owners: true
  exempt_admins: true
  rules:
    caps:
      enabled: true
      max_percentage: 70.0
      min_length: 10
      ignore_commands: true

applications:
  max_applications_per_user: 25  # (not 100)
  max_commands_per_app: 100
  interaction_timeout: 900
  webhook_signature_secret: "<auto-generated-hex>"
  oauth:
    code_expiry_seconds: 600
    token_expiry_seconds: 604800
    refresh_enabled: true
  rate_limits:
    requests_per_minute: 60

docs:
  enabled: true
  path: "/docs/api"  # (not "/docs")
  title: "Plexichat API Documentation"
  description: "Runtime documentation for the Plexichat backend"
  base_url: "https://your-plexichat-host.example/api/v1"
  websocket_url: "wss://your-plexichat-host.example/gateway"
  theme:
    style: "dark"
    primary_color: "#6366f1"
    background_color: "#0b0f19"
    surface_color: "#111827"
    text_color: "#f9fafb"
    muted_color: "#9ca3af"
    accent_color: "#10b981"
    border_color: "#1f2937"
  rate_limit:
    enabled: true
    requests: 60
    window_seconds: 60
  cache:
    enabled: true
    ttl_seconds: 300
  security:
    require_auth: false

feedback:
  enabled: true
  rate_limit:
    max_per_hour: 5
    max_per_day: 20

reports:
  enabled: true

avatars:
  max_size: 512
  max_file_size: 5242880  # 5MB
  allowed_types: ["image/jpeg", "image/png", "image/gif", "image/webp"]
  default_colors:
    - "#e94560"
    - "#4ade80"
    - "#fbbf24"
    - "#60a5fa"
    - "#a78bfa"
    - "#f472b6"

embeds:
  max_embeds_per_message: 10
  max_fields_per_embed: 25
  total_char_limit: 6000
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
    allowed_schemes: ["http", "https"]

telemetry:
  enabled: true  # (not false)
  rate_limit:
    max_per_minute: 10
  retention_days: 30
```

---

## Configuration File Locations

Plexichat looks for configuration files in the following order (first found wins):

1. `--config /path/to/config.yaml` (command line argument)
2. `PLEXICHAT_CONFIG=/path/to/config.yaml` (environment variable)
3. `./config/config.yaml` (relative to application directory)
4. `~/.plexichat/config/config.yaml` (user home directory)
5. Built-in defaults (from `config_defaults.py`)

If no file is present, the server runs with the defaults shown above and logs a warning.

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
    host: "${POSTGRES_HOST:-localhost}"
    password: "${POSTGRES_PASSWORD:-}"
    sslmode: require

redis:
  enabled: true
  host: "${REDIS_HOST:-localhost}"
  password: "${REDIS_PASSWORD:-}"

authentication:
  accounts:
    allow_registration: true
  sessions:
    expire_hours: 168
  security:
    max_failed_attempts: 5
    lockout_duration_minutes: 15

api:
  debug: false
```

---

## Key Name Accuracy

The configuration keys in this document match the actual keys read by the application code. Some keys differ from what you might expect:

- ``registration.enabled`` (`accounts.allow_registration`): Registration is nested under accounts
- ``max_sessions_per_user`` (`sessions.max_per_user`): Sessions use `max_per_user`
- ``session_lifetime_seconds`` (`sessions.expire_hours`): Sessions use hours, not seconds
- ``max_login_attempts`` (`security.max_failed_attempts`): Security uses `max_failed_attempts`
- ``lockout_duration_seconds`` (`security.lockout_duration_minutes`): Lockout uses minutes, not seconds
- ``require_2fa` (admin)` (`admin_ui.require_otp`): Admin UI uses `require_otp`
- ``heartbeat_interval_seconds`` (`websocket.heartbeat_interval_ms`): WebSocket uses milliseconds
- ``messaging.max_message_length`` (`messaging.max_message_length`): Default is 4000, not 2000
- ``bot_multiplier: 0.5`` (`bot_multiplier: 1.5`): Bots get 1.5x the rate limit
- ``voice.sfu_backend: "aiortc"`` (`voice.sfu_backend: "mediasoup"`): Default SFU is mediasoup
- ``search.backend: "sqlite"`` (`search.backend: "sqlite_fts5"`): Search uses FTS5 variant
- ``telemetry.enabled: false`` (`telemetry.enabled: true`): Telemetry defaults on
- ``docs.path: "/docs"`` (`docs.path: "/docs/api"`): Docs served at `/docs/api`
- ``argon2.time_cost: 3`` (`argon2.time_cost: 2`): Lower time cost for faster hashing
- ``argon2.parallelism: 4`` (`argon2.parallelism: 2`): Matches typical CPU cores

---

## Related Documentation

- [Configuration Overview](configuration.md) - Discovery, interpolation, and module-specific guides
- [Authentication Configuration](deployment/configuration/config-authentication.md) - Password policies, 2FA, sessions, account deletion
- [Database Configuration](deployment/configuration/config-database.md) - PostgreSQL/SQLite setup, connection pooling, scaling
- [Redis Configuration](deployment/configuration/config-redis.md) - Caching, session storage, connection pooling
- [Media Configuration](deployment/configuration/config-media.md) - Storage backends, file limits, processing, security
- [Voice Configuration](deployment/configuration/config-voice.md) - SFU backends, STUN/TURN, NAT traversal
- [WebSocket Configuration](deployment/configuration/config-websocket.md) - Gateway, compression, rate limits
- [Search Configuration](deployment/configuration/config-search.md) - Search backends, indexing, discovery
- [Rate Limiting Configuration](deployment/configuration/config-rate-limiting.md) - Global, user, IP, route-level limits
- [API & Server Configuration](deployment/configuration/config-api.md) - CORS, proxies, debug mode, docs paths
