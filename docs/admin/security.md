# Admin security

This guide describes security best practices for Plexichat administrators. Apply these settings to protect admin accounts, enforce access controls, and maintain auditability.

## Secure authentication

### Enforce password policies

Configure strong password policies:

```yaml
admin_ui:
  security:
    password_policy:
      min_length: 12
      require_uppercase: true
      require_lowercase: true
      require_numbers: true
      require_special_chars: true
      prevent_common_passwords: true
      change_interval_days: 90
```

Require admins to rotate passwords when the policy interval expires, and do not reuse shared accounts. There is no `users.force_password_change` scope — to force a username change use `users.force_username_change`; to lock an account or force logout, use `admin.security.sessions` and `admin.security.logout_all`.

### Require two-factor authentication

Require 2FA for every admin account:

1.  Require OTP setup on first login.
2.  Use authenticator apps (avoid SMS when possible).
3.  Generate and store backup codes securely.
4.  Rotate OTP secrets periodically.

### Manage sessions

Configure secure session settings:

```yaml
admin_ui:
  security:
    session:
      max_concurrent_sessions: 3
      timeout_idle_minutes: 30
  session_timeout_minutes: 480
```

Use `admin.security.sessions` to force logout (`POST /api/v1/admin/security/force-logout`) or lock a user (`POST /api/v1/admin/security/lock-user`), and `admin.security.logout_all` to invalidate every session (`POST /api/v1/admin/security/logout-all`). These actions require their respective scopes — do not use a non-existent `users.force_password_change` scope for session management.

## Control access

### Restrict hosts

Restrict admin-panel access to specific hosts:

```yaml
admin_ui:
  host_restriction:
    enabled: true
    allowed_hosts:
      - "127.0.0.1"
      - "localhost"
      - "::1"
      - "admin.yourdomain.com"
```

The server checks `allowed_hosts` on every admin request. Requests from other hosts are rejected before permission checks.

### Block IP addresses

Block suspicious IP addresses:

```yaml
admin_ui:
  blocked_ips:
    - "192.0.2.1"
    - "198.51.100.0/24"
```

At runtime, use the IP-block API with `admin.security.ipblock`:

- `GET /api/v1/admin/security/blocked-ips` — list blocked IPs
- `POST /api/v1/admin/security/block-ip` — block an IP
- `DELETE /api/v1/admin/security/unblock-ip/{ip}` — unblock an IP

### Limit login attempts

Apply rate limiting to prevent brute-force attacks:

```yaml
admin_ui:
  rate_limit:
    max_attempts: 5
    window_seconds: 300
    lockout_seconds: 900
```

## Harden RBAC

### Follow the principle of least privilege

- Assign the minimum permissions needed for each role.
- Prefer specific scopes over broad families. For example, grant `reports.action` to allow reviewing reports rather than a non-existent `reports.resolve`, and grant `users.force_username_change` rather than a non-existent `users.force_password_change`.
- Remove unused roles and permissions.

Valid scopes are defined in `src/core/admin/permissions.py:42` (`PERMISSION_SCOPES`). Common mistakes include:

| Incorrect (does not exist) | Use instead |
|----------------------------|-------------|
| `users.force_password_change` | `users.force_username_change` (force username change) or `admin.security.sessions` / `admin.security.logout_all` (force logout or lock) |
| `servers.ban` | `admin.moderation.bans` (ban or unban users platform-wide) |
| `reports.resolve` | `reports.action` (take action on reports) |

The server returns `400 Invalid or non-delegable permission scope` for unknown scopes.

### Manage roles securely

- Create roles for specific functions; avoid using the super-admin role for daily work.
- Enforce separation of duties for critical operations (for example, separate the roles that can delete servers and purge users).
- Document the purpose of each role and review assignments regularly.
- Remember hierarchy: you can only manage roles and admins at a lower position than your own highest position. See the [RBAC guide](rbac.md).

### Audit permissions

Review regularly:

- Who has access to what.
- What each role can do.
- Recent permission changes (query `GET /api/v1/admin/audit/logs? action_family=admin` or `action_family=role` with `admin.audit.read`).
- Unusual assignments.

The audit log endpoints are `GET /api/v1/admin/audit/logs`, `GET /api/v1/admin/audit/logs/stats`, and `GET /api/v1/admin/audit/logs/export`. The path is `/audit/logs`, not `/audit-log`. See `src/api/routes/admin/audit.py:34`.

## Protect data

### Encrypt sensitive data

- Enable database encryption for sensitive fields.
- Use TLS for all admin-panel connections.
- Encrypt backup files and store encryption keys securely.

### Log admin actions

- Enable comprehensive audit logging for both successful and failed actions.
- Review audit logs regularly. Retrieve them with:

  ```bash
  curl "/api/v1/admin/audit/logs?page=1&per_page=50" \
    -H "Authorization: Bearer <token>"
  ```

- Set up alerting for suspicious activities.

### Secure backups

- Encrypt admin database backups and store them in a secure location.
- Test backup restoration regularly.
- Enforce retention policies.

## Operate securely

### Manage admin accounts

- Use individual admin accounts; never share accounts.
- Disable unused admin accounts promptly.
- Enforce password rotation through policy.
- Revoke access immediately when staff leave.

### Keep the system updated

- Keep Plexichat and its dependencies up to date.
- Test updates in staging before production.
- Monitor security advisories.

### Monitor activity

- Monitor failed login attempts.
- Alert on suspicious admin activities and unusual permission usage.
- Track system performance metrics.

## Respond to incidents

Follow this checklist:

1.  **Identify** — Determine scope and impact.
2.  **Contain** — Limit further damage.
3.  **Eradicate** — Remove the threat.
4.  **Recover** — Restore normal operations.
5.  **Learn** — Document findings and improve controls.

### Compromised admin account

1.  Disable the account immediately.
2.  Force logout of affected sessions (`admin.security.sessions` or `admin.security.logout_all`).
3.  Review audit logs at `GET /api/v1/admin/audit/logs` for suspicious activity.
4.  Rotate secrets and keys.
5.  Notify affected users if necessary.

### Unauthorized access attempt

1.  Block offending IP addresses (`admin.security.ipblock`).
2.  Review audit logs for patterns.
3.  Apply additional rate limiting.
4.  Consider enabling CAPTCHA.
5.  Monitor for continued attempts.

### Data breach

1.  Identify compromised data and assess scope.
2.  Notify affected parties.
3.  Apply additional security measures.
4.  Review and improve security practices.

## Meet compliance requirements

### GDPR

- Minimize data collection.
- Support the right to be forgotten.
- Maintain audit trails.
- Follow data-breach notification procedures.

### SOC 2

- Document security procedures.
- Enforce access controls.
- Enable comprehensive logging.
- Run regular security assessments.

### HIPAA

- Enable access logging.
- Encrypt data at rest and in transit.
- Maintain business-associate agreements.
- Provide regular security training.

## Follow the security checklist

### Daily

- Review failed login attempts.
- Check for unusual admin activity.
- Verify system performance.

### Weekly

- Review audit logs (`GET /api/v1/admin/audit/logs`).
- Check for security updates.
- Verify backup integrity.
- Review role assignments.

### Monthly

- Run a security assessment.
- Review access.
- Check password-rotation compliance.
- Verify compliance status.

### Quarterly

- Run a full security audit.
- Perform penetration testing.
- Provide security training.
- Review policies.

## Related resources

- [OWASP Security Guidelines](https://owasp.org/)
- [NIST Cybersecurity Framework](https://www.nist.gov/cyberframework)
- [CIS Controls](https://www.cisecurity.org/controls/)
- [RBAC guide](rbac.md) — permission scopes and hierarchy
- [Operations guide](operations.md) — daily and weekly security tasks
