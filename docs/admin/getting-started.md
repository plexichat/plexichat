# Get started with Plexichat administration

This guide helps you set up your Plexichat deployment for administration. It covers initial access, required configuration, and the first tasks to complete after installation.

## Before you begin

Make sure you have:

* A running Plexichat server. See the [Deployment guide](../deployment/index.md).
* The initial admin credentials created during setup.
* An authenticator app for 2FA (for example, Authy or Google Authenticator), if `admin_ui.require_otp` is `true`.

Configuration is discovered in this order:

1. `./config/config.yaml`
2. `~/.plexichat/config/config.yaml`
3. Built-in defaults in `src/config_defaults.py`

If no file exists, the server starts with built-in defaults and logs a warning. See [Configuration overview](../configuration.md) for details.

## Access the admin panel

1. Open `https://YOUR_DOMAIN/admin` in your browser. Replace `YOUR_DOMAIN` with your deployment host. The path is controlled by `admin_ui.path` and defaults to `/admin`.
2. Sign in with the initial admin credentials.
3. If prompted, set up 2FA. Scan the QR code with your authenticator app and enter the code to verify. Store your backup codes in a secure location.

Note: If `admin_ui.host_restriction.enabled` is `true`, you can only reach the panel from hosts listed in `admin_ui.host_restriction.allowed_hosts`.

## Configure basic settings

After your first sign-in, configure the following settings. You can change them in `config.yaml` under `admin_ui` or, where available, in **Settings** in the admin panel.

### Security settings

Update `admin_ui.security` and related keys:

```yaml
admin_ui:
  require_otp: true
  force_password_change_first_login: true
  session_timeout_minutes: 480
  security:
    password_policy:
      min_length: 12
      require_uppercase: true
      require_lowercase: true
      require_numbers: true
      require_special_chars: true
      prevent_common_passwords: true
    session:
      max_concurrent_sessions: 3
      timeout_idle_minutes: 30
```

Also review host and network protections:

```yaml
admin_ui:
  host_restriction:
    enabled: true
    allowed_hosts: ["127.0.0.1", "localhost", "::1", "admin.yourdomain.com"]
  rate_limit:
    max_attempts: 5
    window_seconds: 300
    lockout_seconds: 900
```

Global API rate limits are defined separately in `src/config_defaults.py:270`:

* Global: 25,000 requests per 60 seconds, burst 5,000
* Per user: 10,000 requests per 60 seconds, burst 2,500
* Per IP: 5,000 requests per 60 seconds, burst 1,500

### RBAC settings

```yaml
admin_ui:
  rbac:
    enabled: true
    default_role: "super_admin"
```

See [RBAC](rbac.md) for role hierarchy, permission scopes, and wildcard restrictions.

### Audit settings

```yaml
admin_ui:
  audit:
    log_to_file: true
    log_to_database: true
    sensitive_actions_always_db: true
    retention_days: 365
```

See [Audit logging](audit-logging.md) for the `admin_audit_log` schema and retention guidance.

## Complete first-time configuration

### Enable RBAC

1. Go to **Settings > RBAC**.
2. Verify that RBAC is enabled.
3. Review the built-in roles (super admin, support admin, moderation admin, read-only admin).
4. Create custom roles if your organization needs them.
5. Assign roles to each admin. You can only assign roles at or below your own highest role position.

### Configure approval workflows

1. Go to **Settings > Approval Workflows**.
2. Enable approval workflows.
3. Choose which actions require approval. The default is `users.force_purge`, `users.delete`, and `servers.delete`.
4. Set `approval_required_admins` (default `2`) and `approval_timeout_hours` (default `48`).
5. Decide whether to enable `single_admin_bypass` for single-admin deployments.

See [Approval workflows](approval-workflows.md) for the full configuration reference.

### Set up audit logging

1. Go to **Settings > Audit Logging**.
2. Enable both file and database logging for redundancy.
3. Set the retention period to meet your compliance requirements.
4. Verify that sensitive actions are always written to the database.
5. Send a test action and confirm it appears in the audit log.

## Perform common first tasks

### Create an admin account

1. Go to **Admin Management**.
2. Click **Create admin**.
3. Enter the account details and assign a role.
4. Tell the new admin to set up 2FA on first sign-in.

You can also manage roles through the API. See [RBAC](rbac.md#role-assignment) for examples.

### Review system status

1. Open the **Dashboard**.
2. Check active users, total users, and scheduled deletions.
3. Review metrics, endpoint performance, and database query statistics under **Metrics**.
4. Check for alerts and verify database and Valkey connectivity.

Health endpoints for monitoring:

* `GET /health`
* `GET /api/v1/ready` — defined in `health.py:18`
* `GET /api/v1/status`

### Configure notifications

1. Go to **Settings > Notifications**.
2. Configure SMTP settings under `email` if you want email on critical actions.
3. Set `admin_ui.notifications.webhook_url` if you use webhook notifications.
4. Configure alert thresholds and test delivery.

## Understand the dashboard

### Overview section

Displays active users, total users, scheduled deletions, and system performance metrics.

### Metrics section

Displays real-time charts, historical trends, endpoint latency, and database statistics.

### User management

Search for users, edit profiles, manage account status, and add internal notes.

### Server management

View servers, edit settings, manage members, and handle moderation. Channel configuration uses `channel_type` as documented in `src/api/schemas/servers.py:87`.

## Follow security best practices

Do the following immediately after setup:

* Require 2FA for every admin account.
* Enforce a strong password policy.
* Configure host restrictions to limit where the panel is reachable.
* Enable dual audit logging (file and database).
* Rotate the initial admin credentials.

On an ongoing basis:

* Review audit logs regularly.
* Monitor failed sign-in attempts.
* Update Plexichat and its dependencies.
* Review admin roles and remove access promptly when staff changes.
* Back up configuration and the database. Postgres 18 and Valkey 9.1.1 are the versions referenced in the current deployment defaults, running on Python 3.14.

## Troubleshoot common issues

### You cannot access the admin panel

* Verify `admin_ui.enabled` is `true`.
* Check `admin_ui.host_restriction.allowed_hosts` includes your IP or hostname.
* Confirm you use the correct path from `admin_ui.path`.
* Clear browser cache and cookies, or try a different browser.
* Check health: `GET /api/v1/ready`.

### 2FA setup fails

* Verify your device clock is synchronized.
* Try a different authenticator app.
* Confirm you enter the current code before it expires.
* Use a backup code if available.

### You see "Permission denied"

* Check your assigned roles and their permissions in **Admin Management**.
* Verify `admin_ui.rbac.enabled` is `true`.
* Check whether an approval workflow blocks the action. See [Approval workflows](approval-workflows.md).
* Review `admin_audit_log` for the failure reason.

## Configuration reference

The following is a recommended minimum configuration for `admin_ui`:

```yaml
admin_ui:
  enabled: true
  path: "/admin"
  require_otp: true
  force_password_change_first_login: true
  rbac:
    enabled: true
    default_role: "support_admin"
  approval_workflows:
    enabled: true
    single_admin_bypass: true
    require_approval_for:
      - users.force_purge
      - users.delete
      - servers.delete
    approval_required_admins: 2
    approval_timeout_hours: 48
  audit:
    log_to_file: true
    log_to_database: true
    sensitive_actions_always_db: true
    retention_days: 365
  security:
    password_policy:
      min_length: 12
      require_uppercase: true
      require_lowercase: true
      require_numbers: true
      require_special_chars: true
```

For all defaults, see `src/config_defaults.py`.

## Next steps

* Learn how to control access in [RBAC](rbac.md).
* Require multi-admin approval for sensitive actions in [Approval workflows](approval-workflows.md).
* Read [Audit logging](audit-logging.md) to query and export compliance data.
