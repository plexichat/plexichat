# Security Best Practices

This guide covers security considerations for deploying and operating Plexichat in production. It describes the security model, configuration recommendations, and operational practices that protect your deployment and users.

## Authentication Security

### Session Tokens

Plexichat uses bearer token authentication for both REST API and WebSocket connections:

- **User sessions**: `Authorization: Bearer <token>` - generated on login, validated on every request
- **Bot tokens**: `Authorization: Bot <token>` - long-lived, created per-bot, scoped to bot permissions
- **Admin access token**: `X-API-Access-Token: <token>` - optional additional gate for closed deployments (see [Access Tokens](admin-access-tokens.md))

**Session Configuration**

```yaml
authentication:
  sessions:
    token_bytes: 32          # 256 bits of entropy
    expire_hours: 168        # 7 days
    max_per_user: 10         # concurrent sessions
    extend_on_activity: true # auto-extend on use
    extend_threshold_hours: 24
```

**Production Recommendations**

- **Token bytes**: Keep at 32 (default). Reducing below 16 weakens token entropy. Increasing above 32 provides diminishing returns.
- **Session expiry**: The key is `expire_hours`, not `session_lifetime_seconds`. 168 hours (7 days) is the default. Reduce to 24-48 for high-security deployments.
- **Session extension**: With `extend_on_activity: true`, active users aren't logged out. The session is extended when the user was active within the threshold period.
- **Max per user**: Limits concurrent sessions. When exceeded, the oldest session is invalidated. This prevents credential reuse across many devices.

### Password Policy

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

All four complexity requirements should remain enabled in production. The 12-character minimum exceeds current NIST minimum recommendations. The 128-character maximum accommodates passphrases and password managers.

Password hashing uses Argon2id with configurable parameters:

```yaml
encryption:
  argon2:
    time_cost: 2
    memory_cost: 65536      # 64MB
    parallelism: 2
    hash_length: 32
    salt_length: 16
```

Do not reduce `memory_cost` or `time_cost` in production. These values are calibrated to make brute-force attacks expensive. If login performance is a concern, increase `parallelism` rather than reducing `memory_cost`.

### Account Lockout

```yaml
authentication:
  security:
    max_failed_attempts: 5
    lockout_duration_minutes: 15
    token_cache_ttl: 30
    token_verify_rate_limit: 100
    token_binding: false
```

- **Key names**: Use `max_failed_attempts` (not `max_login_attempts`) and `lockout_duration_minutes` (not `lockout_duration_seconds`).
- **Token binding**: When `token_binding: true`, sessions are bound to the client IP. This prevents token theft from a different network location, but breaks for users with rotating IPs (mobile, VPNs). Enable only on controlled networks.
- **Token cache TTL**: How long verified tokens are cached. Lower values increase security but add database load. 30 seconds is a good balance.

### Two-Factor Authentication

Plexichat supports TOTP-based 2FA with configurable parameters:

```yaml
authentication:
  totp:
    issuer: "Plexichat"
    digits: 6
    interval: 30
    backup_code_count: 10
```

**Production Recommendations**

- **Strongly recommend 2FA** for all admin accounts. The admin UI requires OTP by default (`admin_ui.require_otp: true`).
- Make 2FA optional for regular users, but encourage adoption through UI prompts.
- The key is `require_otp`, not `require_2fa`.

---

## Encryption

### At-Rest Encryption

Plexichat supports encryption at multiple levels:

**Message Encryption**

```yaml
messaging:
  encrypt_messages: true
  encrypt_attachments: true
```

When enabled, message content and attachment data are encrypted before storage. This protects data at rest even if the database file is compromised.

**Media Encryption**

```yaml
media:
  encrypt_at_rest: true
  signing_key: "CHANGE_THIS_SIGNING_KEY"
  signing_expiry: 3600
```

- `encrypt_at_rest: true` encrypts media files stored on disk.
- `signing_key` is used to generate time-limited, tamper-proof media URLs. **Change this from the default in production.**
- `signing_expiry: 3600` means media URLs expire after 1 hour. Reduce for higher security, increase for caching convenience.

**Key Rotation**

```yaml
encryption:
  key_rotation_days: 180
  aes_gcm:
    key_length: 32
    nonce_length: 12
    tag_length: 16
```

- Encryption keys are rotated every 180 days by default. Old data is re-encrypted on read (lazy rotation).
- AES-GCM with 32-byte keys (256-bit) provides authenticated encryption - data cannot be read or tampered with without the key.

### Transport Encryption

**HTTPS/TLS**

```yaml
tls:
  enabled: false
```

- Most deployments terminate TLS at a reverse proxy (nginx, Caddy, Traefik). Keep `tls.enabled: false` and configure TLS on your proxy.
- If Plexichat serves TLS directly, set `enabled: true` and provide certificate paths.
- Never run production traffic over unencrypted HTTP.

**CORS Enforcement**

```yaml
api:
  cors_origins:
    - "https://your-app.example.com"
  cors_allow_credentials: true
```

- The key is `cors_origins`, not `allow_origins`. The default is a specific list, not `["*"]`.
- When `cors_allow_credentials: true`, you cannot use wildcard origins - browsers reject it.
- Only list domains that host your legitimate web client.

---

## Admin UI Security

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

### Deployment Considerations

**OTP Requirement**

- `require_otp: true` (not `require_2fa`) requires admins to provide a one-time password for admin panel access, in addition to their session token.
- This provides defense-in-depth: even if a session token is stolen, the admin panel remains protected.

**Host Restriction**

- `host_restriction` limits admin panel access to specific hostnames/IPs. By default, only localhost access is allowed.
- **Production**: If you need remote admin access, add your administrative IP addresses:
  ```yaml
  admin_ui:
    host_restriction:
      enabled: true
      allowed_hosts:
        - "127.0.0.1"
        - "::1"
        - "10.0.0.100"  # admin workstation
  ```
- **Never** set `allowed_hosts: ["0.0.0.0/0"]` - this allows access from any IP.

**Admin Rate Limiting**

- The admin panel has its own rate limiting separate from the API rate limits.
- `max_attempts: 5` within `window_seconds: 300` (5 minutes) triggers `lockout_seconds: 900` (15 minutes).
- These are separate from the account-level lockout in `authentication.security`.

---

## Rate Limiting and Abuse Protection

### Configuration

```yaml
rate_limiting:
  enabled: true
  bot_multiplier: 1.5
  webhook_multiplier: 1.0
  admin_bypass: true
  internal_bypass: true
  bypass_secret: "<auto-generated-hex>"
```

### Security Considerations

**Multipliers**

- `bot_multiplier: 1.5` - bots get 1.5x user limits, not 0.5x. This ensures bots can process events for all their subscribers without hitting limits.
- `webhook_multiplier: 1.0` - webhooks get standard limits.

**Bypass Secret**

- The bypass secret allows internal services to bypass rate limiting. It must be kept secret.
- Never expose it in client-side code or public repositories.
- The default auto-generated value is secure; do not replace it with a predictable value.

**Client Behavior**

Clients should:
- Respect `429 Too Many Requests` responses
- Use exponential backoff on retries
- Avoid concurrent retry storms
- Cache responses where practical

---

## OAuth and Third-Party Login

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

### Security Considerations

**PKCE (Proof Key for Code Exchange)**

- `pkce_enabled: true` (default) protects against authorization code interception attacks. Keep enabled.
- PKCE prevents an attacker who intercepts the authorization code from using it, because they cannot produce the correct code verifier.

**State Management**

```yaml
oauth:
  state_ttl_seconds: 600
  state_token_bytes: 32
  nonce_token_bytes: 32
  cleanup_on_verify: true
  max_states_per_ip: 10
```

- `max_states_per_ip: 10` prevents state-token flooding from a single IP.
- `cleanup_on_verify: true` removes used state tokens to prevent replay attacks.
- `state_ttl_seconds: 600` (10 minutes) - state tokens expire quickly to limit the attack window.

**Client Secrets**

- OAuth client secrets must be stored securely. Use environment variables:
  ```yaml
  oauth:
    google:
      client_id: "${GOOGLE_OAUTH_CLIENT_ID}"
      client_secret: "${GOOGLE_OAUTH_CLIENT_SECRET}"
  ```
- Never commit OAuth secrets to version control.

---

## Self-Test Infrastructure

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

### Security Considerations

- **Never enable in production**: `selftest.enabled: true` creates a test user with known credentials (`selftest_admin` / `SelfTest_Password_123!`). This is a severe security risk.
- `exit_on_failure: false` prevents the server from shutting down if a self-test fails, which could be exploited for denial-of-service.
- `capture_stack_traces: true` in development is fine, but stack traces in production can reveal implementation details.

---

## Sensitive Information Handling

Never expose the following in documentation, client bundles, or public repositories:

- Encryption keys and secret material (`PLEXICHAT_SYSTEM_KEY`, `PLEXICHAT_MEDIA_KEY`, signing keys)
- Database credentials and connection strings
- OAuth client secrets
- Rate limit bypass secrets
- Self-test credentials
- Admin panel paths (if customized from default)
- Internal infrastructure details (proxy IPs, firewall rules)

Use environment variable interpolation (`${VAR_NAME}`) in config files to keep secrets out of version control. See [Configuration Overview](configuration.md) for interpolation syntax.

---

## Security Checklist for Production

**Security Checklist for Production**

- Debug mode: `api.debug: false`
- Session expiry: `authentication.sessions.expire_hours: 24-168`
- Password policy: `authentication.password.*` -- all requirements enabled
- Admin OTP: `admin_ui.require_otp: true`
- Host restriction: `admin_ui.host_restriction.enabled: true`
- CORS origins: `api.cors_origins` -- explicit list, no `*`
- TLS: Reverse proxy or `tls.enabled: true`
- Rate limiting: `rate_limiting.enabled: true`
- Message encryption: `messaging.encrypt_messages: true`
- Media signing key: `media.signing_key` -- changed from default
- Self-test: `selftest.enabled: false`
- Trusted proxies: `api.trusted_proxies` -- configured if behind proxy
- OAuth PKCE: `oauth.pkce_enabled: true`

---

## Security Fixes and Hardening

This section documents recent security improvements and hardening measures applied to Plexichat.

### Media Proxy SSRF Mitigation

**Issue**: The media proxy previously disabled SSL verification for HTTP URLs, creating a potential SSRF (Server-Side Request Forgery) vulnerability where a man-in-the-middle attacker on an internal network could modify proxied content.

**Fix Applied**: SSL verification is now enforced for all proxied URLs (both HTTP and HTTPS). The proxy always uses `verify=True` when fetching external content.

**Impact**: This prevents MITM attacks on proxied content. All external URLs now require valid SSL certificates.

**Configuration**: No configuration changes required. The fix is applied in `src/core/media/security/proxy.py`.

**Additional Mitigations Already in Place**:
- DNS resolution with IP filtering to prevent DNS rebinding
- Disallows redirects (`allow_redirects=False`)
- Content type validation (only allows image types)
- Size limits (max 10MB)
- Hash verification on cached content

### Admin Panel XSS Hardening

**Issue**: The admin panel templates had inconsistent HTML escaping when using `innerHTML` to render user-provided data. While the admin panel is only accessible to authenticated admins, this could be exploited if an admin account is compromised.

**Fix Applied**: All `innerHTML` usage in admin templates now consistently uses the `escapeHtml()` function for user-provided data. This includes:
- User IDs and usernames in deletion management
- Migration details
- Ticket data
- User search results
- Badge names
- Report data
- Access token details
- AutoMod rule names and types

**Impact**: Prevents XSS attacks in the admin panel through user-provided data.

**Files Modified**: `src/api/templates/admin/dashboard.html`

**Note**: The migrations template (`src/api/templates/admin/migrations.html`) uses DOM manipulation methods (`createElement`, `appendChild`, `textContent`) which are inherently safe from XSS.

### SQLite Operational Readiness

**Improvement**: Added SQLite-specific monitoring metrics to the database engine to help operators track SQLite performance and identify when to migrate to PostgreSQL.

**New Metrics Available**:
- Database size in bytes
- WAL (Write-Ahead Log) file size
- Page count and page size
- Database type indicator

**Monitoring Recommendations**:
- Monitor database file growth over time
- Check WAL file size - if large, run `PRAGMA wal_checkpoint(TRUNCATE)`
- Track lock contention through busy timeout occurrences
- Use the provided load testing suite (`pytest src/tests/test_sqlite_load.py`) to validate concurrency limits

**When to Migrate to PostgreSQL**:
- Frequent database lock timeouts in logs
- Degraded performance during peak usage
- Need for horizontal scaling (multiple server instances)
- High write volume (>10 writes/sec)

**Documentation**: See [Database Configuration](deployment/configuration/config-database.md) for detailed SQLite monitoring guidance.

---

## Related Documentation

- [Authentication Configuration](deployment/configuration/config-authentication.md) - Detailed auth settings and deployment considerations
- [API & Server Configuration](deployment/configuration/config-api.md) - CORS, proxies, TLS, debug mode
- [Rate Limiting Configuration](deployment/configuration/config-rate-limiting.md) - Rate limit tuning and bypass configuration
- [Access Tokens](admin/index.md#access-tokens) - API access token gating (in Admin Guide)
- [Security Logout](end-user/security-logout.md) - Logout behavior and session invalidation
- [Access Blocked](end-user/access-blocked.md) - Access denied responses and resolution
