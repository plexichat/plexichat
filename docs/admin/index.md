# Admin documentation

This section contains documentation for administrators who manage Plexichat deployments. Use these guides to configure access control, monitor the system, and maintain compliance.

## Before you begin

You need the following to use the admin panel:

* An admin account created during initial Plexichat setup.
* Access to the admin path, which defaults to `/admin`.
* Two-factor authentication (2FA) configured, if `admin_ui.require_otp` is enabled.
* Network access that satisfies `admin_ui.host_restriction`, if that setting is enabled.

## Admin guides

| Guide | Description |
|---|---|
| [Get started](getting-started.md) | Set up your first admin account and configure core settings. |
| [Role-based access control (RBAC)](rbac.md) | Create roles, assign permissions, and enforce hierarchy. |
| [Approval workflows](approval-workflows.md) | Require multi-admin approval for sensitive operations. |
| [Audit logging](audit-logging.md) | Query and export the audit trail for compliance. |
| [Data export (DSAR)](data-export.md) | Manage GDPR data subject access requests. |
| [Security best practices](security.md) | Harden authentication, sessions, and access control. |
| [User management](user-management.md) | Manage user lifecycles and account states. |
| [Account-purge residue check](purge-residue-check.md) | Prove that a purged account leaves no rows or stored objects behind. |
| [Server management](server-management.md) | Manage servers, members, and moderation. |
| [Operations](operations.md) | Run day-to-day maintenance tasks. |
| [Troubleshooting](troubleshooting.md) | Diagnose and resolve common issues. |

## Related documentation

* [Configuration overview](../configuration.md) — How Plexichat discovers and merges `config.yaml`.
* [Deployment guide](../deployment/index.md) — Install and deploy Plexichat.
* [Default configuration reference](../default-config.md) — Complete list of configuration defaults.
* [Database configuration](../deployment/configuration/config-database.md) — PostgreSQL, pooling, and migrations.

## About the admin panel

The admin panel is a server-rendered UI and a set of REST endpoints under `/api/v1/admin`. It provides:

* **Role-based access control.** Define granular permissions for each admin role. See [RBAC](rbac.md).
* **Audit logging.** Write every admin action to file and database logs. See [Audit logging](audit-logging.md).
* **Approval workflows.** Require multiple admins to approve sensitive actions before they execute. See [Approval workflows](approval-workflows.md).
* **Security controls.** Enforce 2FA, password policies, session limits, host restrictions, and rate limits.
* **Monitoring.** View live metrics, historical data, and recent activity from the dashboard.

### How to access the panel

1. Open `https://YOUR_DOMAIN/admin` in your browser. Replace `YOUR_DOMAIN` with your deployment host.
2. Sign in with your admin credentials.
3. Complete the OTP challenge if prompted.

The API prefix is `/api/v1` by default. This is controlled by `api.api_prefix` in your configuration. Health and readiness endpoints for monitoring are:

* `GET /health`
* `GET /api/v1/ready` — readiness check defined in `health.py:18`
* `GET /api/v1/status` — detailed status
* `GET /gateway` — WebSocket gateway defined in `gateway.py` (`gateway_endpoint`)

## Configuration overview

Admin features are configured under `admin_ui` in your configuration file. The server looks for configuration in this order:

1. `./config/config.yaml`
2. `~/.plexichat/config/config.yaml`
3. Built-in defaults in `src/config_defaults.py`

If no file is present, the server runs with built-in defaults and logs a warning. For details, see [Configuration overview](../configuration.md).

Common `admin_ui` settings include:

```yaml
admin_ui:
  enabled: true
  path: "/admin"
  require_otp: true
  rbac:
    enabled: true
    default_role: "super_admin"
  approval_workflows:
    enabled: true
  audit:
    log_to_file: true
    log_to_database: true
```

For the full list of defaults, see `src/config_defaults.py`.

## Security recommendations

* Use strong, unique passwords and require 2FA for all admin accounts.
* Grant the minimum permissions each admin needs. Follow the principle of least privilege.
* Review `admin_audit_log` regularly for suspicious activity.
* Log out when you finish an admin session and limit concurrent sessions with `admin_ui.security.session.max_concurrent_sessions`.
* Keep Plexichat updated and review audit logs after configuration changes.

## Next steps

* If this is your first time administering Plexichat, start with [Get started](getting-started.md).
* To restrict what each admin can do, see [RBAC](rbac.md).
* To trace who did what and when, see [Audit logging](audit-logging.md).
