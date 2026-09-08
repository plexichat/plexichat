# Admin API

The Plexichat backend exposes an operator-only admin surface in addition to the public REST API. This guide covers the base path, access model, and all route groups mounted by `src/api/routes/admin/__init__.py:13`.

## Base path

All paths in this guide are relative to the configured admin base path. The default is `/admin`, mounted under the API prefix. The effective default base path is:

```
<admin-base> = /api/v1/admin
```

Both `api.api_prefix` and `admin_ui.path` are configurable in `src/config_defaults.py`. If `admin_ui.enabled` is `false`, the admin router is not mounted at runtime.

## Access model

- Most JSON routes require administrator credentials or an admin session token (`Authorization: Bearer <admin-token>`).
- Host restrictions are enforced before handlers run. See `admin_ui.host_restriction` in `src/config_defaults.py:546`.
- Some UI routes return HTML and are intentionally excluded from the OpenAPI schema.
- Permissions are checked against `PERMISSION_SCOPES` in `src/core/admin/permissions.py:42`. The role endpoints reject wildcard scopes such as `*` or `users.*` with `400` (`src/api/routes/admin/roles.py:201`). Do not document `users.force_password_change`, `servers.ban`, or `reports.resolve`; use `users.force_username_change`, `servers.edit`, and `reports.action` instead.
- Destructive actions may require approval when `admin_ui.approval_workflows.enabled` is `true`. By default `users.force_purge`, `users.delete`, and `servers.delete` require approval.

## Route groups

All routes below are prefixed with `<admin-base>`.

### Authentication and session management

| Method | Path | Description |
|---|---|---|
| `POST` | `<admin-base>/login` | Authenticate an admin |
| `POST` | `<admin-base>/verify-otp` | Verify a one-time password |
| `POST` | `<admin-base>/logout` | End the admin session |
| `POST` | `<admin-base>/auth/change-password` | Change own password |
| `POST` | `<admin-base>/auth/force-password-change/{target_admin_id}` | Force a password change for another admin |
| `GET` | `<admin-base>/auth/security-status` | Get 2FA and security status |
| `POST` | `<admin-base>/auth/2fa/begin-setup` | Begin 2FA setup |
| `POST` | `<admin-base>/auth/2fa/disable` | Disable 2FA |
| `POST` | `<admin-base>/auth/2fa/regenerate-backup-codes` | Regenerate backup codes |

Router: `src/api/routes/admin/auth.py`.

### Dashboard, logs, and database health

| Method | Path | Description |
|---|---|---|
| `GET` | `<admin-base>/dashboard` | Operator dashboard data |
| `GET` | `<admin-base>/logs` | List available log files |
| `GET` | `<admin-base>/logs/{filename}` | Fetch a log file |
| `GET` | `<admin-base>/database/pool-health` | Database pool health |

Routers: `dashboard`, `logs`, `database`.

### Tickets and user operations

| Method | Path | Description |
|---|---|---|
| `GET` | `<admin-base>/tickets` | List support tickets |
| `GET` | `<admin-base>/tickets/{ticket_id}` | Get a ticket |
| `PATCH` | `<admin-base>/tickets/{ticket_id}/status` | Update ticket status |
| `GET` | `<admin-base>/tickets/{ticket_id}/notes` | List ticket notes |
| `POST` | `<admin-base>/tickets/{ticket_id}/notes` | Add a ticket note |
| `GET` | `<admin-base>/users/search` | Search users |
| `GET` | `<admin-base>/users/scheduled-deletions` | List scheduled deletions |
| `GET` | `<admin-base>/users/{user_id}` | Get a user profile |
| `PUT` | `<admin-base>/users/{user_id}/tier` | Change a user's tier (`users.tier`) |
| `POST` | `<admin-base>/users/{user_id}/badges/{badge}` | Add a badge (`users.badges`) |
| `DELETE` | `<admin-base>/users/{user_id}/badges/{badge}` | Remove a badge (`users.badges`) |
| `GET` | `<admin-base>/users/{user_id}/notes` | List internal notes (`users.notes`) |
| `POST` | `<admin-base>/users/{user_id}/notes` | Add an internal note (`users.notes`) |
| `POST` | `<admin-base>/users/{user_id}/force-username-change` | Force a username change (`users.force_username_change`) |
| `POST` | `<admin-base>/users/{user_id}/cancel-deletion` | Cancel a scheduled deletion (`users.edit`) |
| `POST` | `<admin-base>/users/{user_id}/delay-deletion` | Extend a grace period (`users.edit`) |
| `POST` | `<admin-base>/users/{user_id}/force-purge` | Immediately purge a scheduled deletion (`users.force_purge`) |

There is no per-user `POST <admin-base>/users/{user_id}/logout-all`. To revoke sessions, use the security routes `POST <admin-base>/security/force-logout` (targeted) or `POST <admin-base>/security/logout-all` (global).

Routers: `tickets`, `users`.

### Admin account management

| Method | Path | Description |
|---|---|---|
| `GET` | `<admin-base>/admin-users` | List admin users |
| `GET` | `<admin-base>/admin-users/{user_id}` | Get an admin user |
| `POST` | `<admin-base>/admin-users` | Create an admin user |
| `PUT` | `<admin-base>/admin-users/{user_id}` | Update an admin user |
| `DELETE` | `<admin-base>/admin-users/{user_id}` | Delete an admin user |
| `POST` | `<admin-base>/admin-users/{user_id}/toggle-status` | Enable or disable an admin user |

Router: `users/admin_users.py`. Role hierarchy is enforced: you can only manage admins and roles below your own position.

### Roles and approval workflows

| Method | Path | Description |
|---|---|---|
| `GET` | `<admin-base>/roles` | List roles |
| `GET` | `<admin-base>/roles/{role_id}` | Get a role |
| `POST` | `<admin-base>/roles` | Create a role (`admin.roles`). Rejects `*` and `*.` scopes |
| `PUT` | `<admin-base>/roles/{role_id}` | Update a role |
| `DELETE` | `<admin-base>/roles/{role_id}` | Delete a role |
| `POST` | `<admin-base>/roles/assign` | Assign a role to an admin |
| `DELETE` | `<admin-base>/roles/{admin_id}/{role_id}` | Revoke a role |
| `GET` | `<admin-base>/admins/{admin_id}/roles` | List an admin's roles |
| `GET` | `<admin-base>/approvals` | List approval requests |
| `POST` | `<admin-base>/approvals/{approval_id}/approve` | Approve a request |
| `POST` | `<admin-base>/approvals/{approval_id}/deny` | Deny a request |

Routers: `roles`, `approvals`. Valid scopes are defined in `src/core/admin/permissions.py:42`.

### Data export (DSAR)

Admin review surface for user data-export requests. See [Data export](../admin/data-export.md).

| Method | Path | Description | Required scope |
|---|---|---|---|
| `GET` | `<admin-base>/data-export` | List export requests | `data_export.read` |
| `GET` | `<admin-base>/data-export/{request_id}` | Get a request | `data_export.read` |
| `POST` | `<admin-base>/data-export/{request_id}/approve` | Approve a request | `data_export.process` |
| `POST` | `<admin-base>/data-export/{request_id}/deny` | Deny a request (`{ "reason": ... }`) | `data_export.process` |
| `POST` | `<admin-base>/data-export/{request_id}/generate` | Generate the export archive | `data_export.process` |

Router: `data_export`.

### Security controls

| Method | Path | Description | Required scope |
|---|---|---|---|
| `GET` | `<admin-base>/security/blocked-ips` | List blocked IPs | `admin.security.ipblock` |
| `POST` | `<admin-base>/security/block-ip` | Block an IP | `admin.security.ipblock` |
| `DELETE` | `<admin-base>/security/unblock-ip/{ip_address}` | Unblock an IP | `admin.security.ipblock` |
| `GET` | `<admin-base>/security/access-tokens` | List API access tokens | `admin.security.tokens` |
| `POST` | `<admin-base>/security/access-tokens` | Create an access token | `admin.security.tokens` |
| `GET` | `<admin-base>/security/access-tokens/{token_id}` | Get a token | `admin.security.tokens` |
| `PATCH` | `<admin-base>/security/access-tokens/{token_id}` | Update a token | `admin.security.tokens` |
| `POST` | `<admin-base>/security/access-tokens/{token_id}/rotate` | Rotate a token | `admin.security.tokens` |
| `POST` | `<admin-base>/security/access-tokens/{token_id}/scopes` | Add token scopes | `admin.security.tokens` |
| `DELETE` | `<admin-base>/security/access-tokens/{token_id}/scopes/{scope_id}` | Remove a token scope | `admin.security.tokens` |
| `POST` | `<admin-base>/security/access-tokens/{token_id}/revoke` | Revoke a token | `admin.security.tokens` |
| `POST` | `<admin-base>/security/access-tokens/{token_id}/unrevoke` | Unrevoke a token | `admin.security.tokens` |
| `GET` | `<admin-base>/security/banned-usernames` | List banned username patterns | `admin.security.usernames` |
| `POST` | `<admin-base>/security/banned-usernames` | Ban a username pattern | `admin.security.usernames` |
| `DELETE` | `<admin-base>/security/banned-usernames/{pattern_id}` | Remove a banned pattern | `admin.security.usernames` |
| `POST` | `<admin-base>/security/force-logout` | Force logout a user | `admin.security.sessions` |
| `POST` | `<admin-base>/security/lock-user` | Lock a user account | `admin.security.sessions` |
| `POST` | `<admin-base>/security/unlock-user` | Unlock a user account | `admin.security.sessions` |
| `POST` | `<admin-base>/security/logout-all` | Invalidate all user sessions | `admin.security.logout_all` |
| `GET` | `<admin-base>/device-trust/overview` | Device-trust and session-duration overview | `admin.security.devices` |

Router: `security` (`device-trust/overview` is served by `device_trust.py`, registered directly on the admin router in `src/api/routes/admin/__init__.py`).

### Moderation and AutoMod

| Method | Path | Description | Required scope |
|---|---|---|---|
| `GET` | `<admin-base>/hash-reports` | List hash reports | `reports.read` |
| `GET` | `<admin-base>/hash-reports/counts` | Count hash reports | `reports.read` |
| `POST` | `<admin-base>/hash-reports/{report_id}/review` | Review a hash report | `reports.action` |
| `GET` | `<admin-base>/message-reports` | List message reports | `reports.read` |
| `GET` | `<admin-base>/message-reports/counts` | Count message reports | `reports.read` |
| `POST` | `<admin-base>/message-reports/{report_id}/review` | Review a message report | `reports.action` |
| `GET` | `<admin-base>/user-reports` | List user reports | `reports.read` |
| `GET` | `<admin-base>/user-reports/counts` | Count user reports | `reports.read` |
| `POST` | `<admin-base>/user-reports/{report_id}/review` | Review a user report | `reports.action` |
| `GET` | `<admin-base>/blocked-hashes` | List blocked hashes | `blocked_hashes.read` |
| `POST` | `<admin-base>/blocked-hashes` | Block a hash | `blocked_hashes.edit` / `admin.moderation.hashes` |
| `DELETE` | `<admin-base>/blocked-hashes/{hash_value}` | Unblock a hash | `blocked_hashes.edit` / `admin.moderation.hashes` |
| `GET` | `<admin-base>/blocked-users` | List blocked users | `blocked_users.read` |
| `POST` | `<admin-base>/blocked-users` | Block a user | `blocked_users.edit` / `admin.moderation.bans` |
| `DELETE` | `<admin-base>/blocked-users/{user_id}` | Unblock a user | `blocked_users.edit` / `admin.moderation.bans` |
| `GET` | `<admin-base>/automod/rules` | List AutoMod rules | `automod.read` / `admin.moderation.rules` |
| `POST` | `<admin-base>/automod/rules` | Create an AutoMod rule | `automod.edit` / `admin.moderation.rules` |
| `PATCH` | `<admin-base>/automod/rules/{rule_id}` | Update a rule | `automod.edit` / `admin.moderation.rules` |
| `DELETE` | `<admin-base>/automod/rules/{rule_id}` | Delete a rule | `automod.edit` / `admin.moderation.rules` |
| `GET` | `<admin-base>/automod/config` | Get AutoMod config | `automod.read` / `admin.moderation.config` |
| `PUT` | `<admin-base>/automod/config` | Update AutoMod config | `admin.moderation.config` |

Router: `moderation`.

### Search, migrations, and bulk operations

| Method | Path | Description | Required scope |
|---|---|---|---|
| `POST` | `<admin-base>/reindex` | Rebuild the search index | `admin.data.reindex` |
| `GET` | `<admin-base>/reindex/status` | Get reindex status | `admin.data.reindex` or `admin.read` |
| `GET` | `<admin-base>/migrations` | List migrations | `admin.migrations.execute` / `config.read` |
| `POST` | `<admin-base>/migrations/run` | Run a migration | `admin.migrations.execute` |
| `POST` | `<admin-base>/migrations/rollback` | Roll back a migration | `admin.migrations.execute` |
| `POST` | `<admin-base>/migrations/apply-all` | Apply all pending migrations | `admin.migrations.execute` |

Routers: `reindex`, `migrations`.

### Bots, artifacts, and licensing

| Method | Path | Description | Required scope |
|---|---|---|---|
| `GET` | `<admin-base>/bots` | List bots | `admin.read` |
| `POST` | `<admin-base>/bots/{bot_id}/approve` | Approve a bot | `admin.edit` |
| `POST` | `<admin-base>/bots/{bot_id}/deny` | Deny a bot | `admin.edit` |
| `GET` | `<admin-base>/artifacts` | List artifacts | `admin.artifacts.manage` |
| `POST` | `<admin-base>/artifacts/{artifact_id}/force-delete` | Force-delete an artifact | `admin.artifacts.manage` |
| `GET` | `<admin-base>/audit` | Query the audit log | `admin.audit.read` |
| `GET` | `<admin-base>/licensing/status` | Get license status | `admin.license.manage` / `admin.read` |
| `POST` | `<admin-base>/licensing/apply` | Apply a license | `admin.license.manage` |

Routers: `bots`, `artifacts`, `audit`, `licensing`.

### Federation, capabilities, and telemetry

| Method | Path | Description | Required scope |
|---|---|---|---|
| `GET` | `<admin-base>/plexijoin/links` | List federation links | `admin.plexijoin.manage` |
| `POST` | `<admin-base>/plexijoin/links` | Create a federation link | `admin.plexijoin.manage` |
| `DELETE` | `<admin-base>/plexijoin/links/{link_id}` | Remove a federation link | `admin.plexijoin.manage` |
| `GET` | `<admin-base>/capabilities` | Get server capabilities | `config.read` |
| `PUT` | `<admin-base>/capabilities` | Update capabilities | `config.edit` |
| `GET` | `<admin-base>/telemetry/stats` | Get telemetry stats | `admin.read` / `metrics.read` |
| `GET` | `<admin-base>/telemetry/history` | Get telemetry history | `admin.read` / `metrics.read` |
| `POST` | `<admin-base>/telemetry/reset` | Reset telemetry | `admin.telemetry.reset` |
| `GET` | `<admin-base>/telemetry/export` | Export telemetry | `admin.read` / `metrics.read` |

Routers: `plexijoin`, `capabilities`, `telemetry`.

### Admin telemetry and UI

| Method | Path | Description |
|---|---|---|
| `GET` | `<admin-base>/` | Admin root (HTML or redirect) |
| `GET` | `<admin-base>/login` | Login page (HTML) |
| `GET` | `<admin-base>/ui` | Admin UI (HTML) |
| `GET` | `<admin-base>/ui-dashboard` | UI dashboard (HTML) |

Router: `ui`. These routes serve HTML and redirect helpers; all other admin routes return JSON.

## Notes

- The admin surface is operator-focused, not a public third-party integration surface. Prefer the public API for integrations.
- UI routes serve HTML, while the JSON routes return `application/json` and are documented in the OpenAPI schema.
- If `admin_ui.enabled` is `false`, the admin router is not mounted and all `<admin-base>` paths return `404`.
