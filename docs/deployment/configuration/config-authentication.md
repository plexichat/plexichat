# Authentication configuration

This guide describes how to configure authentication for Plexichat. Review
each section and adjust values for your security and compliance
requirements.

## Configuration keys

All authentication settings are under the `authentication` key:

```yaml
authentication:
  # All authentication settings go here.
```

## Password policy

```yaml
authentication:
  password:
    min_length: 12
    max_length: 128
    require_uppercase: true
    require_lowercase: true
    require_digit: true
    require_special: true
    guidance_url: /docs/api/end-user/password-guidance.md
```

Password strength defends against credential stuffing. Keep all four
complexity requirements enabled for production. The defaults (12 to 128
characters) accommodate passphrases and password managers. Do not reduce
`max_length`. Changes apply only to new registrations and password changes.

## Account settings

```yaml
authentication:
  accounts:
    username_min_length: 3
    username_max_length: 32
    # username_pattern is not a config key. The pattern is hardcoded as
    # ^[a-zA-Z0-9_]+$.
    age_gate_enabled: false
    minimum_age: 13
    age_verification_type: boolean  # boolean or dob
  email_validation:
    strict: true
    allow_custom_tlds: false
    valid_tlds: []
```

* `username_pattern` is not configurable. To change it, edit the source
  (for example, `src/core/auth/passwords.py`).
* When `age_gate_enabled` is `true`, registration requires age
  verification. Use `boolean` for a confirmation checkbox or `dob` to
  require a date of birth.
* Keep `email_validation.strict` enabled for production. The validator
  checks against 200+ valid TLDs.
* Leave `allow_custom_tlds` disabled unless you use internal custom TLDs.
  If you need them, list them in `valid_tlds`.

Reserved usernames that cannot be registered include `admin`,
`administrator`, `system`, `bot`, `api`, `root`, `null`, and `undefined`.

## Session management

```yaml
authentication:
  sessions:
    token_bytes: 32
    expire_hours: 168  # 7 days
    max_per_user: 10
    extend_on_activity: true
    extend_threshold_hours: 24
```

* `token_bytes` (32) provides 256 bits of entropy. Do not reduce below 16
  for production.
* `expire_hours` (168) is 7 days. For high-security deployments, consider
  24 to 48 hours. For convenience-focused deployments, you can use up to
  720 hours (30 days).
* When `extend_on_activity` is `true`, the server extends the session
  expiry when the user is active within `extend_threshold_hours` (24).
* `max_per_user` (10) limits concurrent sessions. When exceeded, the oldest
  session is invalidated.

Communicate changes to lifetimes in advance and monitor invalidation rates.

## Two-factor authentication

```yaml
authentication:
  totp:
    issuer: Plexichat
    digits: 6
    interval: 30
    backup_code_count: 10
    backup_code_length: 8
```

There is no `enabled` key for TOTP. Two-factor authentication is always
available for users. The admin UI requires OTP by default through
`admin_ui.require_otp: true`. `backup_code_length` defaults to 8 and
values below 8 are clamped. Use the standard `6` digits and `30` second
interval unless you have a specific integration requirement.

Provide user education for setup and backup code storage, and monitor
adoption.

## Account deletion

```yaml
authentication:
  account_deletion:
    enabled: true
    grace_period_days: 30
    reminder_days_before_purge: [7, 1]
    hard_freeze: true
    anonymize_content: true
    audit_log:
      file_path: /root/.plexichat/data/deletion_log.jsonl
      hash_chain_enabled: true
      backup_to_s3: true
      s3_backup_path: audit/deletions/log_backup.jsonl
      halt_on_invalid_audit: true
      rotate_on_broken_chain: false
    reaper:
      interval_hours: 24
      batch_size: 50
      boot_check_enabled: true
```

* The audit log has no `enabled` key. It is active when
  `account_deletion.enabled` is `true`.
* `file_path` defaults to `~/.plexichat/data/deletion_log.jsonl`. Ensure the
  directory exists and is writable by the application.
* `backup_to_s3` and `s3_backup_path` enable an S3 backup of the log when
  media S3 is configured.
* `halt_on_invalid_audit` (default `true`) refuses to start when the hash
  chain verification fails.
* `rotate_on_broken_chain` (default `false`) renames a broken file to
  `<path>.broken-<timestamp>` and starts a fresh chain.
* `reminder_days_before_purge` (default `[7, 1]`) controls when reminders
  are sent.
* `hard_freeze` (default `true`) freezes the account immediately on request.
* `anonymize_content` (default `true`) replaces messages with
  `[This message was sent by a deleted user]` instead of deleting them.

Enable account deletion for deployments subject to GDPR or CCPA, keep the
30-day grace period unless you have a specific reason to change it, and test
the deletion flow before production.

## Passkeys

```yaml
authentication:
  passkeys:
    enabled: true
    rp_name: Plexichat
    rp_id: ${PASSKEY_RP_ID:-localhost}
    origin: ${PASSKEY_ORIGIN:-http://localhost}
    challenge_ttl_seconds: 300
    cleanup_interval_hours: 24
```

Options:

* `enabled` - Whether passkey registration and authentication are available.
* `rp_name` - Relying party name shown during registration.
* `rp_id` - Effective domain for passkeys, for example `api.plexichat.com`.
  Do not use `0.0.0.0` or `127.0.0.1` in production.
* `origin` - Full origin including scheme, for example
  `https://api.plexichat.com`. It must exactly match the browser origin.
* `challenge_ttl_seconds` (300) - Lifetime of registration and
  authentication challenges.
* `cleanup_interval_hours` (24) - How often expired challenges are cleaned
  up by the Account Reaper.

Passkeys require HTTPS in production and a modern browser with WebAuthn
support. Set `rp_id` and `origin` with environment variables
`PASSKEY_RP_ID` and `PASSKEY_ORIGIN`. These values cannot change after
users register passkeys without invalidating them.

If registration or authentication fails, verify that `rp_id` matches your
domain, `origin` matches the browser origin, HTTPS is valid, and the
challenge has not expired.

## Security settings

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
    session_cookie_secure: true
```

* `max_failed_attempts` (5) - Consecutive failures before lockout.
* `lockout_duration_minutes` (15) - Value is in minutes, not seconds.
* `lockout_min_distinct_ips` (2) - Failures must come from at least this
  many distinct IPs within `failed_login_window_minutes` before lockout.
  This prevents a single host from denying service to a known username.
* `failed_login_window_minutes` (15) - Window for counting distinct IPs.
* `token_cache_ttl` (30) - Seconds to cache verified tokens before
  re-verification.
* `token_verify_rate_limit` (100) - Token verifications per minute.
* `token_binding` (false) - When `true`, binds tokens to the client IP.
  Keep disabled for users with rotating IPs.
* `session_cookie_secure` (true) - HttpOnly session cookie Secure flag.
  Set to `false` only for plain-HTTP localhost development.

Implement unlock procedures for legitimate users and monitor lockout rates
for targeted attacks.

## Registration

```yaml
authentication:
  accounts:
    allow_registration: true
    require_email_verification: false
    max_bots_per_user: 5
    username_min_length: 3
    username_max_length: 32
  encryption:
    require_secure_source: true
    media_key: ${PLEXICHAT_MEDIA_KEY:-}
  reset_password:
    client_url: ""
    path: /reset-password
```

* Use `accounts.allow_registration`, not `registration.enabled`.
* For public communities, keep `allow_registration: true`. For private or
  enterprise deployments, set it to `false` and use invites or SSO.
* When you enable `require_email_verification`, configure SMTP in the
  `email` section.
* `max_bots_per_user` (5) limits bot accounts. Bot tokens use
  `authentication.bots.token_bytes: 48`.
* `require_secure_source` (true) accepts session tokens only from secure
  sources. Keep enabled for production.
* `media_key` is the media encryption key. Set it with
  `PLEXICHAT_MEDIA_KEY`. If unset, the key is derived from the signing key.
* `reset_password.client_url` (empty) is the web client origin, for example
  `https://chat.example.com`. When set, password reset emails contain a
  fully qualified link. `reset_password.path` defaults to
  `/reset-password`.

## Data subject access requests

```yaml
authentication:
  dsar:
    enabled: true
    require_admin_review: true
    default_format: json
    export_formats: [json, zip]
    max_export_size_mb: 500
    retention_days: 7
    pending_expiry_days: 30
    local_path: /root/.plexichat/data/exports/dsar
    audit_log:
      file_path: /root/.plexichat/data/dsar_audit_log.jsonl
      hash_chain_enabled: true
      backup_to_s3: true
      s3_backup_path: audit/dsar/log_backup.jsonl
      halt_on_invalid_audit: true
      rotate_on_broken_chain: false
    harvester:
      interval_hours: 24
      boot_check_enabled: true
      batch_size: 20
```

DSAR handles GDPR Article 20 portability. The storage backend and S3
settings are inherited from the `media` block.

## Related documentation

* [Default configuration reference](../../default-config.md)
* [Database configuration](config-database.md)
* [Security best practices](../../security.md)
* [Deployment guide](../getting-started.md)
