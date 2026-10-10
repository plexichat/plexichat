# Troubleshoot the admin panel

This guide helps you diagnose and fix common issues with the Plexichat admin panel.

## Fix authentication issues

### You cannot log in

**Symptoms:** The login page loads but authentication fails.

**Possible causes:**

- Incorrect username or password.
- Account is locked due to rate limiting.
- Two-factor authentication (2FA) failure.
- Host restriction blocks your IP.
- Session or cookie issue.

**Try this:**

1.  Verify your username and password.
2.  Wait for the lockout period to expire if you are rate-limited (check `admin_ui.rate_limit`).
3.  Verify that your 2FA code is correct and your device time is synchronized.
4.  Verify that your IP is in `admin_ui.host_restriction.allowed_hosts`.
5.  Clear browser cache and cookies, or try a different browser.

### 2FA codes are rejected

**Symptoms:** One-time password (OTP) codes are rejected.

**Possible causes:**

- Device time is out of sync.
- Incorrect OTP secret.
- Authenticator app issue.

**Try this:**

1.  Sync your device time with network time.
2.  Try a different authenticator app.
3.  Use a backup code if available.
4.  Ask a super admin to reset your 2FA.

### Your session expires too quickly

**Symptoms:** You are logged out frequently.

**Possible causes:**

- Session timeout is too short.
- IP address changes (VPN or network switch).
- Browser clears cookies.

**Try this:**

1.  Increase `admin_ui.security.session.timeout_idle_minutes` and `session_timeout_minutes`.
2.  Check for IP changes.
3.  Disable cookie clearing for the admin domain.

## Fix permission issues

### You see "access denied" or "insufficient permissions"

**Symptoms:** The API returns `403` or the UI shows a permission error.

**Possible causes:**

- Missing required scope.
- Role is not assigned correctly.
- RBAC is disabled.
- An approval workflow is blocking the action.

**Try this:**

1.  Check your assigned roles at `GET /api/v1/admin/admins/{id}/roles`.
2.  Verify the required scope for the action. Check `src/core/admin/permissions.py:42` (`PERMISSION_SCOPES`) and do not use non-existent scopes such as those in the following table:

    | Incorrect (does not exist) | Use instead |
    |----------------------------|-------------|
    | `users.force_password_change` | `users.force_username_change` or `admin.security.sessions` |
    | `servers.ban` | `admin.moderation.bans` |
    | `reports.resolve` | `reports.action` |

3.  Verify that `admin_ui.rbac.enabled` is `true`.
4.  Check whether the action requires approval (`admin.approvals`).
5.  Review the audit log at `GET /api/v1/admin/audit/logs` (requires `admin.audit.read`; the path is `/audit/logs`, not `/audit-log` — see `src/api/routes/admin/audit.py:34`).

### You cannot access a section

**Symptoms:** Some admin sections are missing or return `403`.

**Possible causes:**

- Missing specific scope.
- Section is disabled in configuration.

**Try this:**

1.  Check which scope the section requires. For example, audit logs require `admin.audit.read`, blocked users require `admin.moderation.bans`, and reviewing reports requires `reports.action`.
2.  Verify that the section is enabled in your configuration.
3.  Ask a super admin to update your roles.

## Fix performance issues

### The dashboard loads slowly

**Symptoms:** The dashboard takes a long time to load.

**Possible causes:**

- Large dataset.
- Database performance issue.
- Network latency.
- Server resource constraints.

**Try this:**

1.  Check database performance and slow queries.
2.  Review system resources.
3.  Check network connectivity.
4.  Reduce the data range or time period.
5.  Consider database optimization (indexes, connection pool, cache hit rate).

### Charts show stale data

**Symptoms:** Dashboard charts do not update.

**Possible causes:**

- JavaScript error.
- API connectivity issue.
- Browser caching.
- WebSocket issue.

**Try this:**

1.  Refresh the page.
2.  Check the browser console for errors.
3.  Clear the browser cache.
4.  Verify API and WebSocket connectivity.

## Fix data issues

### You cannot find a user

**Symptoms:** Search does not return the user.

**Possible causes:**

- User was deleted.
- Incorrect search criteria or user-ID typo.
- Database sync issue.

**Try this:**

1.  Verify your search criteria.
2.  Check whether the user was deleted or purged.
3.  Try different search terms.
4.  Check database integrity and review the audit log.

### Data is inconsistent

**Symptoms:** Different views show different values.

**Possible causes:**

- Caching issue.
- Database replication lag.
- Concurrent modifications.

**Try this:**

1.  Refresh the page and clear the cache.
2.  Check database consistency and the data source.
3.  Report the issue if it persists.

## Fix configuration issues

### Settings do not save

**Symptoms:** Configuration changes do not persist.

**Possible causes:**

- File permission issue.
- Database write error.
- Validation error.
- Concurrent modification.

**Try this:**

1.  Check file permissions.
2.  Verify database connectivity.
3.  Review validation errors in the API response.
4.  Try again after a short delay.

### Changes do not take effect

**Symptoms:** Configuration is saved but has no effect.

**Possible causes:**

- Service restart is required.
- Caching issue.
- Incorrect configuration value.

**Try this:**

1.  Restart the service.
2.  Clear the cache.
3.  Verify configuration syntax and check for conflicting settings.

## Fix audit-logging issues

### Actions are not logged

**Symptoms:** Some admin actions do not appear in the audit log.

**Possible causes:**

- Audit logging is disabled.
- Database connection issue.
- Logging configuration error.

**Try this:**

1.  Verify that audit logging is enabled (`admin_ui.audit`).
2.  Check database connectivity.
3.  Review file permissions and logging configuration.
4.  Query the correct endpoint: `GET /api/v1/admin/audit/logs` with `admin.audit.read`. Do not query `/audit-log` — that path does not exist.

### Audit logs are missing details

**Symptoms:** Audit entries are incomplete.

**Possible causes:**

- Logging level is too low.
- Data sanitization removes fields.
- Database schema issue.

**Try this:**

1.  Check the logging level.
2.  Review sanitization settings.
3.  Verify the database schema.
4.  Check for logging errors and update the configuration.

### How to query audit logs

```bash
# List recent logs (paginated)
curl "/api/v1/admin/audit/logs?page=1&per_page=50" \
  -H "Authorization: Bearer <token>"

# Filter by action family and status
curl "/api/v1/admin/audit/logs?action_family=reports&status=success" \
  -H "Authorization: Bearer <token>"

# Export as CSV
curl "/api/v1/admin/audit/logs/export?format=csv" \
  -H "Authorization: Bearer <token>" -o audit_logs.csv
```

Required permission: `admin.audit.read`. For endpoint details, see `src/api/routes/admin/audit.py:34`.

## Fix approval-workflow issues

### Approval requests are not created

**Symptoms:** Sensitive actions run without approval.

**Possible causes:**

- Approval workflows are disabled.
- Action is not in `require_approval_for`.
- Single-admin bypass is active.
- Missing permission.

**Try this:**

1.  Verify that `approval_workflows.enabled` is `true`.
2.  Check that the action is in `require_approval_for`.
3.  Review `single_admin_bypass` — when only one admin exists and bypass is enabled, approval is skipped.
4.  Check admin permissions.

### Approvals are not counted

**Symptoms:** Approvals do not increment.

**Possible causes:**

- You already approved the request.
- Request has expired.
- Permission issue.

**Try this:**

1.  Check whether you already approved.
2.  Verify that the request has not expired.
3.  Check approval permissions (`admin.approvals`) and database state.

## Fix system issues

### The service does not start

**Symptoms:** The admin-panel service fails to start.

**Possible causes:**

- Configuration error.
- Database connection issue.
- Port conflict.
- Resource constraints.

**Try this:**

1.  Check configuration syntax.
2.  Verify database connectivity.
3.  Check for port conflicts.
4.  Review system resources and error logs.

### Memory usage is high

**Symptoms:** The admin panel uses excessive memory.

**Possible causes:**

- Memory leak.
- Large datasets or cache.

**Try this:**

1.  Review memory-usage patterns.
2.  Reduce cache size.
3.  Check for leaks and optimize configuration.
4.  Restart the service.

## Get help

### When to contact support

- The issue persists after troubleshooting.
- You suspect a security issue.
- You see data-integrity issues or system outages.
- You need help with configuration.

### What to include

- Plexichat version (`GET /api/v1/version`).
- Exact error message.
- Steps to reproduce.
- Relevant configuration (redact secrets).
- Audit-log excerpts and system logs.

### Collect diagnostic information

```bash
# System information
uname -a
python --version

# Plexichat version
curl http://localhost:8000/api/v1/version

# Recent audit logs
curl "http://localhost:8000/api/v1/admin/audit/logs?per_page=20" \
  -H "Authorization: Bearer <token>"
```

## Prevent issues

### Maintain the system

- Review system logs weekly.
- Check disk space monthly.
- Update software regularly.
- Review audit logs monthly.
- Test backup restoration quarterly.

### Monitor

- Alert on critical errors.
- Monitor system resources and database performance.
- Track failed login attempts and approval-workflow status.

### Document

- Document custom configurations and changes.
- Maintain runbooks and troubleshooting notes.
- Keep contact information up to date.
