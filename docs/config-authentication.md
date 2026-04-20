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
- **Balancing Security and Usability**: If users complain about password requirements, consider providing a password strength meter in your client application rather than relaxing requirements.

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
    username_pattern: "^[a-zA-Z0-9_]+$"
    email_validation:
      strict: true
      allow_custom_tlds: false
      valid_tlds: []
```

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
    max_sessions_per_user: 10
    session_lifetime_seconds: 2592000  # 30 days
    refresh_token_lifetime_seconds: 7776000  # 90 days
    device_tracking_enabled: true
```

### Deployment Considerations

**Why This Matters**

Session management balances security (shorter lifetimes, fewer sessions) against user experience (longer lifetimes, more concurrent devices). Compromised sessions are a common attack vector.

**Production Recommendations**

**Session Lifetime (30 days default)**

- **Standard Deployment**: 30 days is appropriate for most applications. Users rarely want to re-authenticate more frequently.
- **High-Security Deployment**: Consider reducing to 7-14 days for sensitive environments.
- **Low-Security Deployment**: Can increase to 60-90 days for convenience-focused applications.

**Refresh Token Lifetime (90 days default)**

- Refresh tokens allow users to stay logged in without re-entering credentials. The 90-day default balances security and convenience.
- For high-security deployments, reduce to 30 days to limit the window for token theft.
- Ensure refresh tokens are stored securely (HttpOnly cookies or secure storage on client).

**Maximum Sessions (10 default)**

- Limits concurrent logins per user. Oldest session is invalidated when limit is exceeded.
- **Standard Deployment**: 10 sessions accommodates users with multiple devices (phone, tablet, desktop, work computer).
- **Enterprise Deployment**: Consider reducing to 5 to limit account sharing.
- **Consumer Deployment**: Can increase to 20 for power users with many devices.

**Device Tracking**

- Keep enabled for production. Tracks user agent and IP address for security auditing.
- Essential for detecting suspicious login patterns (e.g., login from new country).
- Required for session revocation and security notifications.

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
    enabled: true
    issuer: "Plexichat"
    digits: 6
    interval: 30
    backup_code_count: 10
    backup_code_length: 8
    backup_code_max_checks: 3
```

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
    anonymize_content: true
    audit_log:
      enabled: true
      file_path: "/var/lib/plexichat/audit/deletion_log.jsonl"
      hash_chain_enabled: true
    reaper:
      interval_hours: 24
      batch_size: 50
      boot_check_enabled: true
```

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

## Security Settings

Security thresholds for account lockout and password change policies.

### Configuration

```yaml
authentication:
  security:
    max_login_attempts: 5
    lockout_duration_seconds: 900  # 15 minutes
    password_change_cooldown_seconds: 86400  # 24 hours
```

### Deployment Considerations

**Why This Matters**

Account lockout prevents brute-force and credential stuffing attacks. Password change cooldowns prevent rapid password cycling that could bypass security measures.

**Production Recommendations**

**Login Attempts (5 default)**

- **Standard Deployment**: 5 attempts is appropriate. Allows for genuine typos while blocking automated attacks.
- **High-Security Deployment**: Reduce to 3 attempts for sensitive environments.
- **Low-Security Deployment**: Can increase to 10 for convenience-focused applications.

**Lockout Duration (15 minutes default)**

- **Standard Deployment**: 15 minutes is appropriate. Blocks automated attacks while not overly punishing users.
- **High-Security Deployment**: Consider 30-60 minutes for sensitive environments.
- **Low-Security Deployment**: Can reduce to 5 minutes for convenience.

**Password Change Cooldown (24 hours default)**

- Prevents users from rapidly cycling through passwords to bypass account lockout.
- **Standard Deployment**: 24 hours is appropriate.
- **High-Security Deployment**: Consider 7 days to prevent password reuse patterns.
- **Low-Security Deployment**: Can reduce to 1 hour for convenience.

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
  registration:
    enabled: true
    require_email_verification: false
    default_role: "user"
```

### Deployment Considerations

**Why This Matters**

Registration settings determine who can create accounts and what verification is required. This impacts user growth, spam prevention, and security.

**Production Recommendations**

**Enable Registration**

- **Public Applications**: Keep enabled for open communities.
- **Private Applications**: Disable and use invite-only registration or admin-created accounts.
- **Enterprise Applications**: Disable and integrate with SSO or directory services.

**Email Verification**

- **Recommended**: Enable for production deployments to prevent spam accounts and ensure email deliverability.
- When enabling email verification, requires email configuration (SMTP settings) to be functional. See [Email Configuration](default-config.md#email-settings) in the default configuration reference.
- **Trade-off**: Adds friction to registration but significantly reduces spam and fake accounts.

**Default Role**

- **Standard Deployment**: "user" role is appropriate for most deployments.
- **Custom Roles**: Configure custom roles if your application has different user tiers or permissions.
- **Admin Accounts**: Never assign admin role as default. Create admin accounts manually after deployment.

**Operational Notes**

- If disabling registration, implement an alternative account creation mechanism (admin panel, invite system).
- Monitor new registration rates for anomalies that may indicate automated account creation attacks.
- Consider implementing CAPTCHA for registration forms (not currently built-in).

---

## Related Documentation

- [Default Configuration Reference](default-config.md) - Complete configuration reference
- [Database Configuration](config-database.md) - User data storage and session persistence
- [Security Best Practices](security.md) - Authentication security expectations
- [Deployment Guide](deployment.md) - Production deployment procedures
