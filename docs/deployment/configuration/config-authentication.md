# Authentication Configuration

This guide covers authentication configuration for deploying Plexichat in production. Authentication settings directly impact security, user experience, and compliance obligations. Carefully review each section and adjust values according to your security requirements and regulatory environment.

## Configuration Location

All authentication settings are nested under the `authentication` key in your configuration file:

```yaml
authentication:
  # All authentication settings go here
```

## Password Policy

Password policies determine the strength requirements for user passwords. Stronger passwords improve security but may increase user friction and support requests.

### Configuration

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

### Deployment Considerations

**Why This Matters**

Password strength is your first line of defense against credential stuffing attacks and unauthorized access. Weak passwords are the most common entry point for account compromises.

**Production Recommendations**

- **Minimum Length**: 12 characters is the current industry standard. Consider increasing to 14-16 for high-security deployments. Each additional character exponentially increases brute-force difficulty.
- **Maximum Length**: 128 characters accommodates passphrases and password managers. Do not reduce this value.
- **Complexity Requirements**: All four requirements (uppercase, lowercase, digits, special) should remain enabled for production. Disabling any requirement significantly weakens security.

**Security Trade-offs**

- **Stricter Requirements**: Better security, higher user friction, more password reset requests
- **Looser Requirements**: Better user experience, significantly higher risk of account compromise

**Operational Notes**

- Changes to password policy only affect new registrations and password changes. Existing users are not forced to update passwords.
- Consider implementing a password expiration policy for high-security environments (requires custom implementation).

---

## Account Settings

These settings control username and email validation during registration.

### Configuration

```yaml
authentication:
  accounts:
    username_min_length: 3
    username_max_length: 32
    # username_pattern is NOT a config key. The pattern is hardcoded in the
    # server as ^[a-zA-Z0-9_]+$. To change it, modify the source code.
    age_gate_enabled: false
    minimum_age: 13
    age_verification_type: "boolean"  # "boolean" or "dob"
  email_validation:
    strict: true
    allow_custom_tlds: false
    valid_tlds: []
```

**Note**: `username_pattern` is not a config key. The username regex is hardcoded in `src/core/auth/passwords.py` as `^[a-zA-Z0-9_]+$`. To change the allowed characters, modify the source code directly.

**Age Gate**: When `age_gate_enabled: true`, registration requires age verification. In `"boolean"` mode, users simply confirm they meet the minimum age. In `"dob"` mode, users must provide a date of birth, and the server verifies they meet `minimum_age` (default: 13).

### Deployment Considerations

**Username Validation**

- **Length Limits**: The default 3-32 character range is appropriate for most deployments. Adjust if your community has specific naming conventions.
- **Pattern**: The default pattern allows only alphanumeric characters and underscores. This prevents confusion and impersonation attempts. Consider allowing hyphens if needed for your user base.

**Email Validation**

**Why Strict Validation Matters**

Email validation prevents registration with disposable or malformed email addresses, reducing spam accounts and ensuring deliverability of important notifications.

**Production Recommendations**

- **Strict Mode**: Keep enabled for production. This validates TLDs against a comprehensive list of 200+ valid TLDs.
- **Custom TLDs**: Disable (`allow_custom_tlds: false`) unless you have a legitimate use case (e.g., internal network with custom TLDs).
- **Custom TLD List**: If you need custom TLDs, explicitly list them in `valid_tlds` rather than allowing all custom TLDs.

**Reserved Usernames**

The following usernames are always reserved and cannot be registered: admin, administrator, system, bot, api, root, null, undefined. This prevents impersonation and confusion with system accounts.

---

## Session Management

Session settings control token lifetimes, concurrent sessions, and device tracking. These directly impact security and user experience.

### Configuration

```yaml
authentication:
  sessions:
    token_bytes: 32
    expire_hours: 168  # 7 days
    max_per_user: 10
    extend_on_activity: true
    extend_threshold_hours: 24
```

### Deployment Considerations

**Why This Matters**

Session management balances security (shorter lifetimes, fewer sessions) against user experience (longer lifetimes, more concurrent devices). Compromised sessions are a common attack vector.

**Production Recommendations**

**Session Expiry (168 hours / 7 days default)**

- **Standard Deployment**: 7 days is the default. Sessions are extended on activity when `extend_on_activity` is enabled.
- **High-Security Deployment**: Consider reducing to 24-48 hours for sensitive environments.
- **Low-Security Deployment**: Can increase to 720 hours (30 days) for convenience-focused applications.

**Session Extension on Activity**

- When `extend_on_activity: true` (default), sessions are automatically extended when the user is active within the threshold period.
- `extend_threshold_hours: 24` means the session expiry is pushed forward if the user was active within the last 24 hours.
- This prevents active users from being logged out while still enforcing expiry for inactive sessions.

**Token Bytes (32 default)**

- Controls the entropy of session tokens. 32 bytes provides 256 bits of entropy, which is cryptographically secure.
- Do not reduce below 16 bytes for production deployments.

**Maximum Sessions (10 default)**

- Limits concurrent sessions per user. When exceeded, the oldest session is invalidated.
- **Standard Deployment**: 10 sessions accommodates users with multiple devices (phone, tablet, desktop, work computer).
- **Enterprise Deployment**: Consider reducing to 5 to limit account sharing.
- **Consumer Deployment**: Can increase to 20 for power users with many devices.

**Operational Notes**

- When reducing session lifetimes, communicate changes to users in advance.
- Consider implementing "remember me" options with different lifetimes for different security levels.
- Monitor session invalidation rates—high rates may indicate users exceeding session limits.

---

## Two-Factor Authentication (TOTP)

TOTP (Time-based One-Time Password) provides an additional security layer using authenticator apps like Google Authenticator, Authy, or Microsoft Authenticator.

### Configuration

```yaml
authentication:
  totp:
    issuer: "Plexichat"
    digits: 6
    interval: 30
    backup_code_count: 10
```

**Note**: There is no `enabled` key for TOTP. Two-factor authentication is always available for users to opt into. The admin UI requires OTP by default via `admin_ui.require_otp: true`. The `backup_code_length` and `backup_code_max_checks` keys exist in the code but are not exposed as config keys -- they use hardcoded defaults (8 characters, 3 max checks).

### Deployment Considerations

**Why 2FA Matters**

2FA is the single most effective security measure against account compromise. Even if passwords are stolen, accounts remain protected without the second factor.

**Production Recommendations**

**Enable 2FA**

- **Strongly Recommended**: Keep enabled for all production deployments.
- **Optional 2FA**: You can make 2FA optional for users but require it for administrators (requires role-based configuration in your application logic).
- **Mandatory 2FA**: For high-security deployments, consider making 2FA mandatory for all users.

**Issuer Name**

- Set to your application or organization name. This is what users see in their authenticator app.
- Use a recognizable name to help users identify which account the code is for.

**Digits and Interval**

- **6 digits, 30 seconds** is the industry standard. Do not change unless you have a specific integration requirement.
- 8-digit codes provide slightly better security but are harder for users to enter quickly.
- Longer intervals (60 seconds) give users more time to enter codes but increase replay attack windows.

**Backup Codes**

- **10 codes** is appropriate for most deployments. Users can print these codes and store them securely.
- **8-character codes** provide good entropy while being manageable for users.
- Users should be instructed to store backup codes in a secure location (not in their email).

**Security Trade-offs**

- **Mandatory 2FA**: Maximum security, higher user friction, potential user resistance
- **Optional 2FA**: Good security for users who enable it, lower overall security posture
- **No 2FA**: Poor security, not recommended for production

**Operational Notes**

- Provide clear user education on setting up 2FA, including QR code scanning and backup code storage.
- Offer multiple recovery options (backup codes, account recovery process) for users who lose access.
- Monitor 2FA adoption rates and consider incentives or requirements for high-value accounts.

---

## Account Deletion

Account deletion settings control the user-initiated deletion process, grace periods, and GDPR compliance features.

### Configuration

```yaml
authentication:
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
      batch_size: 50
      boot_check_enabled: true
```

**Note**: The audit log config does NOT have an `enabled` key. The audit log is always active when `account_deletion.enabled: true`. The `file_path` defaults to `~/.plexichat/audit/deletion_log.jsonl`. The `backup_to_s3` and `s3_backup_path` keys control optional S3 backup of the deletion audit log. The `reminder_days_before_purge` key controls when reminder notifications are sent before the grace period expires (default: 7 days and 1 day before). The `hard_freeze` key freezes the account immediately upon deletion request (default: true).

### Deployment Considerations

**Why This Matters**

GDPR and similar regulations require organizations to honor user data deletion requests. Proper configuration ensures compliance and provides audit trails.

**Production Recommendations**

**Enable Account Deletion**

- **Legal Requirement**: Must be enabled for deployments subject to GDPR, CCPA, or similar regulations.
- **User Expectation**: Modern users expect the ability to delete their accounts.
- **Internal Applications**: May disable for enterprise deployments where account data must be retained.

**Grace Period (30 days default)**

- Gives users time to cancel accidental deletion requests.
- **Standard Deployment**: 30 days is appropriate and aligns with best practices.
- **Extended Grace**: Consider 60-90 days for high-value accounts to prevent accidental loss.
- **Short Grace**: Can reduce to 7 days for applications where rapid deletion is expected.

**Content Anonymization vs Deletion**

- **Anonymize (true)**: Messages are replaced with "[This message was sent by a deleted user]". Preserves conversation continuity while removing user identity.
- **Delete (false)**: Messages are completely removed. May break conversation threads but provides stronger privacy.
- **Recommendation**: Anonymization is usually preferred for community applications to preserve context.

**Audit Log**

- **Enable**: Required for GDPR compliance. Provides tamper-evident record of all deletion operations.
- **File Path**: Ensure the directory exists and is writable by the application. Consider placing on a separate filesystem with appropriate permissions.
- **Hash Chaining**: Provides cryptographic proof that the log has not been tampered with. Keep enabled for compliance.

**Reaper Configuration**

- **Interval (24 hours)**: How often the background task purges expired accounts. Daily is appropriate for most deployments.
- **Batch Size (50)**: Maximum accounts processed per cycle. Adjust based on your expected deletion volume and database performance.
- **Boot Check**: Detects "zombie" accounts if database was restored from backup. Keep enabled.

**Operational Notes**

- Ensure the audit log directory has appropriate filesystem permissions (restricted to application user).
- Monitor audit log file size and implement rotation if necessary (not currently automated).
- Test the deletion process before production deployment to verify all user data is properly handled.
- Provide users with clear information about what data is deleted and what is retained during the grace period.

---

## Passkeys (WebAuthn/FIDO2)

Passkeys provide passwordless authentication using biometric authentication (fingerprint, face recognition) or device PINs. Built on the WebAuthn/FIDO2 standard, passkeys offer phishing-resistant security and better user experience than traditional passwords.

### Configuration

```yaml
authentication:
  passkeys:
    enabled: true
    rp_name: "Plexichat"
    rp_id: "${PASSKEY_RP_ID:-localhost}"
    origin: "${PASSKEY_ORIGIN:-http://localhost}"
    challenge_ttl_seconds: 300
    cleanup_interval_hours: 24
```

### Configuration Options

**enabled** (boolean, default: `true`)
- Controls whether passkey registration and authentication are available
- Set to `false` to disable passkey functionality entirely
- When disabled, passkey-related API endpoints return errors

**rp_name** (string, default: `"Plexichat"`)
- The Relying Party name displayed to users during passkey registration
- Should be your application or organization name
- Users see this name in their device's passkey management interface

**rp_id** (string, required for production)
- The Relying Party ID - the domain where passkeys are registered
- Must be the effective domain (e.g., `api.plexichat.com`), not the bind address
- Critical for security: passkeys only work on this domain
- **Do not use** bind addresses like `0.0.0.0`, `127.0.0.1`, or `localhost` in production
- Set via environment variable: `PASSKEY_RP_ID=api.plexichat.com`

**origin** (string, required for production)
- The full origin (protocol + host + port) where the application is accessed
- Must exactly match the browser's origin for passkey verification to succeed
- Examples:
  - `https://api.plexichat.com` (production)
  - `http://localhost:8000` (development)
  - `https://app.plexichat.com` (if using a different domain)
- Set via environment variable: `PASSKEY_ORIGIN=https://api.plexichat.com`

**challenge_ttl_seconds** (integer, default: `300`)
- How long registration/authentication challenges remain valid (in seconds)
- Default 300 seconds (5 minutes) is appropriate for most deployments
- Reduce to 120 seconds for higher security
- Increase to 600 seconds for better user experience on slow connections

**cleanup_interval_hours** (integer, default: `24`)
- How often expired challenges are cleaned up from the database
- Cleanup is performed by the Account Reaper background task
- Default 24 hours is appropriate for most deployments
- Reduce to 12 hours for high-volume deployments

### Deployment Considerations

**Why Passkeys Matter**

Passkeys represent the future of authentication:
- **Phishing-resistant**: Passkeys only work on the legitimate website/domain
- **No passwords to remember**: Users authenticate with biometrics or PINs
- **Stronger security**: Cryptographic keys are much harder to steal than passwords
- **Better UX**: Face ID, Touch ID, or Windows Hello instead of typing passwords
- **Cross-device**: Use a phone to authenticate on a computer via QR code

**Production Requirements**

**Critical: Correct RP ID and Origin**

The `rp_id` and `origin` settings are critical for passkey functionality:

- **RP ID** must be your effective domain (e.g., `api.plexichat.com`)
- **Origin** must be the full URL with protocol (e.g., `https://api.plexichat.com`)
- Mismatched configuration will cause all passkey operations to fail
- These values cannot be changed after users register passkeys (would invalidate existing passkeys)

**HTTPS Requirement**

- Passkeys require HTTPS in production (browser security requirement)
- Development can use HTTP with `localhost` origin
- For production, ensure valid SSL/TLS certificates are configured

**Browser and Device Support**

Passkeys require:
- Modern browser with WebAuthn support (Chrome 67+, Firefox 60+, Safari 13+, Edge 18+)
- Device with biometric authentication or security key support
- Operating system: macOS 12+, Windows 10/11, iOS 16+, Android 9+

**Production Recommendations**

**Enable Passkeys**

- **Recommended**: Enable for all production deployments
- **Optional**: Can be offered alongside password authentication
- **Passwordless**: Can be the primary authentication method with password as backup

**Configuration**

- Set `rp_id` to your production domain
- Set `origin` to your full production URL with HTTPS
- Use environment variables for sensitive configuration
- Document the configuration for your operations team

**User Education**

- Provide clear documentation on setting up passkeys
- Explain the benefits (security, convenience)
- Offer guidance on multiple device registration
- Provide recovery options (password reset, backup codes)

**Security Trade-offs**

- **Passkeys Only**: Maximum security, may exclude users without compatible devices
- **Passkeys + Passwords**: Best balance, allows gradual migration
- **Passwords Only**: Lowest security, not recommended for modern deployments

**Operational Notes**

- Test passkey registration and authentication in your production environment before rollout
- Monitor passkey adoption rates and user feedback
- Ensure the Account Reaper is running to clean up expired challenges
- Keep the `webauthn` library updated (`webauthn==2.5.0` or later)
- Consider offering hardware security keys for users without biometric devices

**Migration Strategy**

For existing deployments:

1. **Phase 1**: Enable passkeys alongside passwords
2. **Phase 2**: Encourage users to register passkeys
3. **Phase 3**: Make passkeys the default login method
4. **Phase 4**: Optionally require passkeys for new registrations

**Troubleshooting**

**Passkey Registration Fails**

- Verify `rp_id` matches your domain
- Verify `origin` matches the browser's origin (check browser console)
- Ensure HTTPS is configured correctly
- Check browser console for WebAuthn errors

**Passkey Authentication Fails**

- Verify the passkey was registered on the same domain
- Check that the challenge hasn't expired (default 5 minutes)
- Verify the user's device supports WebAuthn
- Check browser compatibility

**Challenges Not Cleaning Up**

- Verify the Account Reaper is running
- Check the `cleanup_interval_hours` setting
- Monitor database size for challenge table growth

---

## Security Settings

Security thresholds for account lockout and password change policies.

### Configuration

```yaml
authentication:
  security:
    max_failed_attempts: 5
    lockout_duration_minutes: 15
    token_cache_ttl: 30
    token_verify_rate_limit: 100
    token_binding: false
```

### Deployment Considerations

**Why This Matters**

Account lockout prevents brute-force and credential stuffing attacks. Password change cooldowns prevent rapid password cycling that could bypass security measures.

**Production Recommendations**

**Failed Attempts Threshold (5 default)**

- The key is `max_failed_attempts`, not `max_login_attempts`. After this many consecutive failed login attempts, the account is locked.
- **Standard Deployment**: 5 attempts allows for genuine typos while blocking automated attacks.
- **High-Security Deployment**: Reduce to 3 attempts for sensitive environments.
- **Low-Security Deployment**: Can increase to 10 for convenience-focused applications.

**Lockout Duration (15 minutes default)**

- The key is `lockout_duration_minutes`, not `lockout_duration_seconds`. The value is in minutes, not seconds.
- **Standard Deployment**: 15 minutes blocks automated attacks while not overly punishing users.
- **High-Security Deployment**: Consider 30-60 minutes for sensitive environments.
- **Low-Security Deployment**: Can reduce to 5 minutes for convenience.

**Token Cache TTL (30 seconds default)**

- How long verified tokens are cached before re-verification against the database. Lower values increase security but add database load.
- **Standard Deployment**: 30 seconds is a good balance.
- **High-Security Deployment**: Reduce to 5-10 seconds.

**Token Verify Rate Limit (100 per minute default)**

- Maximum number of token verifications per minute. Prevents token brute-forcing.
- **Standard Deployment**: 100 is appropriate for most deployments.

**Token Binding (false default)**

- When enabled, session tokens are bound to the client's IP address. Provides additional security but breaks for users with rotating IPs (mobile networks, VPNs).
- **Standard Deployment**: Keep disabled unless all users have stable IPs.
- **High-Security Deployment**: Enable if users are on a controlled network.

**Security Trade-offs**

- **Stricter Lockout**: Better protection against brute force, higher support burden for locked-out users
- **Looser Lockout**: Better user experience, higher vulnerability to automated attacks

**Operational Notes**

- Implement account unlock procedures for legitimate users who are locked out.
- Monitor lockout rates—high rates may indicate targeted attacks against your deployment.
- Consider CAPTCHA integration for repeated failed login attempts (not currently built-in).

---

## Registration

Controls user registration behavior and initial account setup.

### Configuration

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
    media_key: "${PLEXICHAT_MEDIA_KEY:-}"  # Optional, derived from signing key if not set
```

### Deployment Considerations

**Why This Matters**

Registration settings determine who can create accounts and what verification is required. This impacts user growth, spam prevention, and security.

**Production Recommendations**

**Registration**

- The key is `accounts.allow_registration`, not `registration.enabled`. Registration is controlled under the `accounts` section.
- **Public Applications**: Keep `allow_registration: true` for open communities.
- **Private Applications**: Set to `false` and use invite-only registration or admin-created accounts.
- **Enterprise Applications**: Set to `false` and integrate with SSO or directory services.

**Email Verification**

- **Recommended**: Enable for production deployments to prevent spam accounts and ensure email deliverability.
- When enabling email verification, requires email configuration (SMTP settings) to be functional. See the `email` section in the [Default Configuration Reference](../../default-config.md).
- **Trade-off**: Adds friction to registration but significantly reduces spam and fake accounts.

**Bot Accounts**

- `max_bots_per_user: 5` limits how many bot accounts each user can create. Bots use separate authentication (see `authentication.bots.token_bytes: 48`).
- **Standard Deployment**: 5 bots per user is sufficient for most use cases.
- **Developer-Friendly Deployment**: Increase to 10-25 for communities with many custom bots.

**Encryption**

- `require_secure_source: true` ensures session tokens are only accepted from secure (HTTPS) sources. Keep enabled in production.
- `media_key` is a separate encryption key for media files at rest. Set via the `PLEXICHAT_MEDIA_KEY` environment variable. If not set, the key is automatically derived from the signing key (backwards compatible with existing encrypted media).

**Operational Notes**

- If disabling registration, implement an alternative account creation mechanism (admin panel, invite system).
- Monitor new registration rates for anomalies that may indicate automated account creation attacks.

---

## Related Documentation

- [Default Configuration Reference](../../default-config.md) - Complete configuration reference
- [Database Configuration](deployment/configuration/config-database.md) - User data storage and session persistence
- [Security Best Practices](../../security.md) - Authentication security expectations
- [Deployment Guide](../getting-started.md) - Production deployment procedures
