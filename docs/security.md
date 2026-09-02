# Security best practices

This guide describes the security model for Plexichat and the configuration and operational practices that protect your deployment and users.

## Authentication

### Session tokens

Plexichat uses bearer-token authentication for REST API and WebSocket connections:

*   **User sessions** — `Authorization: Bearer <token>` — Generated on login and validated on every request.
*   **Bot tokens** — `Authorization: Bot <token>` — Long-lived tokens that are scoped to bot permissions.
*   **Admin access token** — `X-API-Access-Token: <token>` — Optional gate for closed deployments. See [Access tokens](admin-access-tokens.md).

#### Session configuration

```yaml
authentication:
  sessions:
    token_bytes: 32          # 256 bits of entropy
    expire_hours: 168        # 7 days
    max_per_user: 10         # Concurrent sessions
    extend_on_activity: true # Auto-extend on use
    extend_threshold_hours: 24
```

Recommendations:

*   **Token bytes** — Keep the default `32`. Values below `16` weaken entropy. Values above `32` give diminishing returns.
*   **Session expiry** — The key is `expire_hours`, not `session_lifetime_seconds`. The default is `168` hours (7 days). For high-security deployments, use `24–48` hours.
*   **Session extension** — When `extend_on_activity` is `true`, active users stay logged in. The session extends when the user was active within the threshold.
*   **Max per user** — When the limit is exceeded, the oldest session is invalidated. This prevents credential reuse across many devices.

### Password policy

```yaml
authentication:
  password:
    min_length: 12
    max_length: 128
    require_uppercase: true
    require_lowercase: true
    require_digit: true
    require_special: true
```

Keep all four complexity requirements enabled in production. The 12-character minimum exceeds current NIST minimums. The 128-character maximum accommodates passphrases and password managers.

Password hashing uses Argon2id:

```yaml
encryption:
  argon2:
    time_cost: 3
    memory_cost: 65536      # 64 MB
    parallelism: 2
    hash_length: 32
    salt_length: 16
```

Do not reduce `memory_cost` or `time_cost` in production. If login performance is a concern, increase `parallelism` instead of reducing memory. See `src/config_defaults.py:465`.

### Account lockout

```yaml
authentication:
  security:
    max_failed_attempts: 5
    lockout_duration_minutes: 15
    lockout_min_distinct_ips: 2
    failed_login_window_minutes: 15
    token_cache_ttl: 30
    token_verify_rate_limit: 100
    token_binding: false
```

*   Use `max_failed_attempts` (not `max_login_attempts`) and `lockout_duration_minutes` (not `lockout_duration_seconds`).
*   `lockout_min_distinct_ips` requires failures from at least 2 distinct IPs within the window, which prevents a single host from denying service to a known username.
*   `token_binding` — When `true`, sessions are bound to the client IP. This prevents token theft from a different network but breaks for users with rotating IPs (mobile, VPNs). Enable only on controlled networks.
*   `token_cache_ttl` — How long verified tokens stay cached. Lower values improve security but add database load. `30` seconds is a good balance.

### Two-factor authentication

Plexichat supports TOTP-based 2FA:

```yaml
authentication:
  totp:
    issuer: "Plexichat"
    digits: 6
    interval: 30
    backup_code_count: 10
    backup_code_length: 8
```

*   Require 2FA for all admin accounts. The admin UI requires OTP by default (`admin_ui.require_otp: true`).
*   Make 2FA optional for regular users, but encourage adoption through UI prompts.
*   The key is `require_otp`, not `require_2fa`. See `src/config_defaults.py:539`.

## Encryption

### Message and media encryption

```yaml
messaging:
  encrypt_messages: true
  encrypt_attachments: true

media:
  encrypt_at_rest: true
  signing_key: "CHANGE_THIS_SIGNING_KEY"
  signing_expiry: 3600
```

*   When `encrypt_messages` is `true` (the default), message content and attachments are encrypted before storage with the dedicated `message_keyring.json`.
*   When `encrypt_at_rest` is `true` (the default), media files on disk are encrypted.
*   `signing_key` generates time-limited, tamper-proof media URLs. Change it from the default in production. The server auto-generates it with `secrets.token_hex(32)` and persists it to `config/config.yaml` on first startup if it is still `CHANGE_THIS_SIGNING_KEY`. See `src/cli/main.py:74`.
*   `signing_expiry: 3600` means media URLs expire after 1 hour. Reduce for higher security or increase for caching convenience.

### Key rotation

```yaml
encryption:
  key_rotation_days: 180
  aes_gcm:
    key_length: 32
    nonce_length: 12
    tag_length: 16
```

Keys rotate every 180 days by default. Existing data is re-encrypted on read (lazy rotation). AES-256-GCM provides authenticated encryption.

### Key encryption keys (KEKs)

Plexichat uses a hierarchical system with Key Encryption Keys to protect keyrings that store encryption keys.

#### Keyring architecture

Plexichat maintains three keyrings in `~/.plexichat/data/`:

*   `system_keyring.json` — System-level keys.
*   `message_keyring.json` — Message encryption keys.
*   `file_keyring.json` — File and media encryption keys.

Each keyring is encrypted with a KEK and supports versioned key rotation.

#### KEK sources (priority order)

1.  **Environment variable** (highest priority):
    *   `PLEXICHAT_SYSTEM_KEY` — KEK for `system_keyring.json`.
    *   `PLEXICHAT_MESSAGE_KEY` — KEK for `message_keyring.json`.
    *   `PLEXICHAT_MEDIA_KEY` — KEK for `file_keyring.json`.
2.  **HSM (Hardware Security Module)** — PKCS#11 devices with non-extractable AES-256 keys.
3.  **TPM 2.0** — Machine-local hardware security.
4.  **Machine-local file** (fallback) — `~/.plexichat/data/.machine_key`. Used only when no other source is available.

If a keyring-specific KEK is not set, the system falls back to `PLEXICHAT_SYSTEM_KEY` for that keyring.

#### HSM configuration

```yaml
encryption:
  hsm:
    enabled: false
    library_path: "/usr/lib/softhsm/libsofthsm2.so"
    slot_id: 0
    pin: ""
    key_label: "plexichat_kek"
```

#### Set KEKs

Set KEKs as Base64-encoded 32-byte keys:

```bash
export PLEXICHAT_SYSTEM_KEY="<64-character-hex-system-kek>"
export PLEXICHAT_MESSAGE_KEY="<64-character-hex-message-kek>"
export PLEXICHAT_MEDIA_KEY="<64-character-hex-media-kek>"
```

#### Require secure sources

For production, enforce secure KEK sources:

```yaml
authentication:
  encryption:
    require_secure_source: true
```

When enabled, the server fails to start if no TPM, HSM, or environment-variable KEK is available.

#### Migrate KEKs

When you change KEKs, use the built-in migration tool:

```bash
# Validate current keyrings
python main.py migrate-kek --kek-validate --kek-all

# Migrate a keyring from the shared system KEK to a dedicated KEK
python main.py migrate-kek --kek-keyring message_keyring.json --kek-old-env PLEXICHAT_SYSTEM_KEY --kek-new-env PLEXICHAT_MESSAGE_KEY

# Migrate all keyrings to dedicated KEKs
python main.py migrate-kek --kek-keyring system_keyring.json --kek-old-env PLEXICHAT_SYSTEM_KEY --kek-new-env PLEXICHAT_SYSTEM_KEY
python main.py migrate-kek --kek-keyring message_keyring.json --kek-old-env PLEXICHAT_SYSTEM_KEY --kek-new-env PLEXICHAT_MESSAGE_KEY
python main.py migrate-kek --kek-keyring file_keyring.json --kek-old-env PLEXICHAT_SYSTEM_KEY --kek-new-env PLEXICHAT_MEDIA_KEY

# Dry run
python main.py migrate-kek --kek-keyring message_keyring.json --kek-old-env PLEXICHAT_SYSTEM_KEY --kek-new-env PLEXICHAT_MESSAGE_KEY --kek-dry-run

# Rollback
python main.py migrate-kek --kek-rollback --kek-keyring message_keyring.json
```

The tool creates automatic backups, validates both KEKs, supports dry runs, provides rollback, and logs all operations.

#### Backup and recovery

The Docker `backup` container (restic-based) includes `~/.plexichat/` in every snapshot. See `docker/README.md`.

Backup strategy:

1.  **Restic snapshots** — Daily snapshots that contain `~/.plexichat/` (including keyrings).
2.  **KEK backup** — Store KEK values in a password manager. They are not inside restic snapshots.
3.  **Secondary repository** — Configure `BACKUP_SECONDARY_REPO` for off-site replication.
4.  **Test restores** — Periodically restore keyrings from snapshots.
5.  **Migration backups** — The migration tool creates timestamped backups during rotation.

To recover:

1.  Restore keyrings from the latest snapshot:

    ```bash
    docker exec plexichat-backup-1 /scripts/backup.sh restore-latest /tmp/restore
    # Copy keyrings from /tmp/restore/plexichat-home/data/
    ```

2.  Set KEK environment variables.
3.  Run `python main.py migrate-kek --kek-validate --kek-all`.
4.  If validation fails, re-encrypt with current KEKs.

Best practices for KEKs:

*   Never commit KEKs to version control. Use environment variables or a secret manager.
*   Rotate KEKs every 6–12 months with the migration tool.
*   Generate 32-byte keys with cryptographic randomness.
*   Use different KEKs for development, staging, and production.
*   Limit KEK access to authorized personnel.
*   Log KEK access and migration events.

### Transport encryption

```yaml
tls:
  enabled: false
```

Most deployments terminate TLS at a reverse proxy (nginx, Caddy, Traefik). Keep `tls.enabled: false` and configure TLS on the proxy. If Plexichat serves TLS directly, set `enabled: true` and provide certificate paths. Never serve production traffic over unencrypted HTTP.

### Encryption-at-rest defaults

*   **Argon2id** uses `time_cost: 3` (OWASP 2024 minimum), `memory_cost: 65536` (64 MiB), `parallelism: 2`, `hash_length: 32`, and `salt_length: 16`. See `src/config_defaults.py:465`.
*   **`*_encrypted` columns** for medium-sensitivity user data (email, phone, display name, bio) hold AES-256-GCM ciphertext from the system keyring. Legacy plaintext columns remain for backward compatibility.
*   **`media.signing_key`** is auto-generated with `secrets.token_hex(32)` and persisted on first startup if it is still `CHANGE_THIS_SIGNING_KEY`.
*   **Message content** is encrypted at rest when `messaging.encrypt_messages: true` (the default).
*   **Media files** are encrypted at rest when `media.encrypt_at_rest: true` (the default). Media URLs are signed and expire after `media.signing_expiry` seconds.

### CORS

```yaml
api:
  cors_origins:
    - "https://your-app.example.com"
  cors_allow_credentials: true
```

*   The key is `cors_origins`, not `allow_origins`. The default is an explicit list, not `["*"]`.
*   When `cors_allow_credentials` is `true`, browsers reject wildcard origins.
*   List only domains that host your web client. See `src/config_defaults.py:133`.

## Admin UI

### Configuration

```yaml
admin_ui:
  enabled: true
  path: "/admin"
  require_otp: true
  host_restriction:
    enabled: true
    allowed_hosts: ["127.0.0.1", "localhost", "::1"]
  rate_limit:
    max_attempts: 5
    window_seconds: 300
    lockout_seconds: 900
```

### Deployment considerations

*   **OTP requirement** — `require_otp: true` (not `require_2fa`) requires admins to provide a one-time password for admin panel access in addition to their session token.
*   **Host restriction** — By default, only localhost can access the admin panel. For remote admin access, add your administrative IP:

    ```yaml
    admin_ui:
      host_restriction:
        enabled: true
        allowed_hosts:
          - "127.0.0.1"
          - "::1"
          - "10.0.0.100"  # Admin workstation
    ```

    Never set `allowed_hosts: ["0.0.0.0/0"]`.

*   **Rate limiting** — The admin panel has separate rate limiting from the API. `max_attempts: 5` within `window_seconds: 300` (5 minutes) triggers `lockout_seconds: 900` (15 minutes). These limits are independent of `authentication.security`.

## Rate limiting and abuse protection

### Configuration

```yaml
rate_limiting:
  enabled: true
  bot_multiplier: 1.5
  webhook_multiplier: 1.0
  admin_bypass: true
  internal_bypass: true
```

*   `bot_multiplier: 1.5` — Bots get 1.5× user limits so they can process events for subscribers.
*   `webhook_multiplier: 1.0` — Webhooks get standard limits.
*   Internal services bypass limits with the runtime-generated internal secret (`X-Plexichat-Internal-Secret`) or `X-RateLimit-Bypass` with `hmac.compare_digest` verification. See `src/api/middleware/rate_limiting.py:102`.

Clients should respect `429 Too Many Requests`, use exponential backoff, avoid retry storms, and cache responses where practical.

For full limits and route-level caps, see [Rate limits](rate-limits.md) and [Rate limiting configuration](deployment/configuration/config-rate-limiting.md).

## OAuth and third-party login

### Configuration

```yaml
oauth:
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
```

### Security considerations

*   **PKCE** — Keep `pkce_enabled: true` (the default). PKCE prevents authorization-code interception. See `src/api/routes/auth/oauth.py:186`.
*   **State management**:

    ```yaml
    oauth:
      state_ttl_seconds: 600
      state_token_bytes: 32
      nonce_token_bytes: 32
      cleanup_on_verify: true
      max_states_per_ip: 10
      state_cookie_required: true
    ```

    *   `max_states_per_ip: 10` prevents state-token flooding.
    *   `cleanup_on_verify: true` removes used state tokens to prevent replay.
    *   `state_ttl_seconds: 600` (10 minutes) limits the attack window.
    *   `state_cookie_required: true` binds the OAuth state to the browser with an `HttpOnly` cookie. See `src/api/routes/auth/oauth.py:90`.

*   **Client secrets** — Store secrets securely with environment-variable interpolation:

    ```yaml
    oauth:
      google:
        client_id: "${GOOGLE_OAUTH_CLIENT_ID}"
        client_secret: "${GOOGLE_OAUTH_CLIENT_SECRET}"
    ```

    Never commit OAuth secrets to version control.

*   **Redirect URI allow-list** — The server enforces an exact-match allow-list for `redirect_uri` (`src/api/routes/auth/oauth.py:50`). Configure `oauth.<provider>.allowed_redirect_uris` and enable `oauth.allow_localhost_redirects` only for development.

## Self-test infrastructure

### Configuration

```yaml
selftest:
  enabled: false
  run_on_startup: false
  exit_on_failure: false
  capture_stack_traces: true
  excluded_endpoints:
    - "/api/v1/auth/logout"
    - "/api/v1/admin/logout"
```

*   Self-test is production-safe. It uses long, randomly generated passwords and only creates and deletes its own test users (`selftest_admin` / `selftest_admin_other`). It never touches real admin accounts. All test resources are cleaned up through API calls. See `src/config_defaults.py:506`.
*   `exit_on_failure: false` prevents shutdown on self-test failure, which avoids denial-of-service.
*   `capture_stack_traces: true` is useful in development. Set it to `false` in production to avoid revealing implementation details.
*   Self-test cleanup is strictly non-destructive: it only removes rows owned by its `selftest_%` accounts and resources recorded by the current run. There are no whole-table wipes and no disposable-database escape hatch; the suite is safe to run on a live deployment at any time. See `src/core/selftest/services/cleanup.py`.

## Sensitive information handling

Never expose the following in documentation, client bundles, or public repositories:

*   Encryption keys and secret material (`PLEXICHAT_SYSTEM_KEY`, `PLEXICHAT_MEDIA_KEY`, signing keys).
*   Database credentials and connection strings.
*   OAuth client secrets.
*   Internal infrastructure details (proxy IPs, firewall rules).

Use environment-variable interpolation (`${VAR_NAME}`) in config files. See [Configuration overview](configuration.md).

## Production security checklist

Before you go live, verify:

*   `api.debug: false`
*   `authentication.sessions.expire_hours: 24–168`
*   `authentication.password.*` — All requirements enabled.
*   `admin_ui.require_otp: true`
*   `admin_ui.host_restriction.enabled: true`
*   `api.cors_origins` — Explicit list, no `*`.
*   TLS — Reverse proxy or `tls.enabled: true`.
*   `rate_limiting.enabled: true`
*   `messaging.encrypt_messages: true`
*   `media.signing_key` — Changed from default.
*   `selftest.enabled: true` is safe for production (random passwords, API cleanup, no admin data loss).
*   `api.trusted_proxies` — Configured when behind a proxy.
*   `oauth.pkce_enabled: true`
*   `authentication.encryption.require_secure_source: true` — Enforce TPM, HSM, or environment-variable KEKs.

## Security fixes and hardening

### Media proxy SSRF mitigation

The media proxy previously disabled SSL verification for HTTP URLs, which created an SSRF risk where a man-in-the-middle attacker on an internal network could modify proxied content. SSL verification is now enforced for all proxied URLs (`verify=True`). See `src/core/media/security/proxy.py`.

Additional mitigations:

*   DNS resolution with IP filtering to prevent DNS rebinding.
*   No redirects (`allow_redirects=False`).
*   Content-type validation (image types only).
*   Size limits (max 10 MB).
*   Hash verification on cached content.

### Admin panel XSS hardening

Admin templates previously used `innerHTML` inconsistently when rendering user-provided data. All `innerHTML` usage in `src/api/templates/admin/dashboard.html` now uses `escapeHtml()` for user-provided data, including user IDs, migration details, ticket data, search results, badge names, report data, token details, and AutoMod rule names.

The migrations template (`src/api/templates/admin/migrations.html`) uses `createElement`, `appendChild`, and `textContent`, which are safe from XSS.

### SQLite operational readiness

The database engine exposes SQLite monitoring metrics such as database size, WAL size, page count, and type. See `src/core/database/monitoring.py` and `src/core/database/core/metrics.py`.

Monitor file growth, check WAL size (`PRAGMA wal_checkpoint(TRUNCATE)` when large), track lock contention through busy-timeout occurrences, and run `pytest src/tests/test_sqlite_load.py` to validate concurrency.

Migrate to PostgreSQL when you see frequent lock timeouts, degraded peak performance, need for horizontal scaling, or sustained write volume above ~10 writes per second.

## Related documentation

*   [Authentication configuration](deployment/configuration/config-authentication.md)
*   [API and server configuration](deployment/configuration/config-api.md)
*   [Rate limiting configuration](deployment/configuration/config-rate-limiting.md)
*   [Access tokens](admin/index.md#access-tokens)
*   [Security logout](end-user/security-logout.md)
*   [Access blocked](end-user/access-blocked.md)
