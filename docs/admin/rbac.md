# Role-based access control (RBAC)

This guide describes how Plexichat controls admin access with roles and permission scopes. Use it to create roles, assign permissions, and enforce the principle of least privilege.

The real permission scopes are defined in `src/core/admin/permissions.py` (`PERMISSION_SCOPES`), and role-creation validation is implemented in `src/api/routes/admin/roles.py`.

## How permissions work

Plexichat checks permissions with hierarchy matching:

1.  If the role has the internal super-admin marker (`*`), the check passes immediately. You cannot assign `*` through the role API.
2.  The system checks for an exact match (for example, `users.read`).
3.  If the required permission is a sub-scope of a granted wildcard family that exists in `PERMISSION_SCOPES`, the check passes (for example, a role with `reports.*` satisfies `reports.read` and `reports.action` when matched hierarchically). Note that the role-create and role-update endpoints currently reject any scope that ends with `.*` — see [Create and update roles](#create-and-update-roles).
4.  An explicit `false` denies only that specific scope, not its sub-scopes.

Only scopes that exist in `PERMISSION_SCOPES` are valid. The server returns `400 Invalid or non-delegable permission scope` if you submit an unknown scope, `*`, or a wildcard scope such as `users.*` when you create or update a role.

## Role hierarchy

Each admin role has a `position` value that enforces hierarchy. You can only manage roles and admins at a lower position than your own highest role position.

| Position | Authority |
|----------|-----------|
| 100 | Super admin (highest) |
| 80 | Senior admin |
| 60 | Standard admin |
| 40 | Junior admin |
| 10 | Support / read-only (lowest) |

System roles (`is_system = 1`) have fixed high positions and you cannot delete or demote them. The system determines your authority by the maximum position across all your assigned roles.

## Default roles

### Super admin

- Has the internal super-admin marker (`*`) and bypasses all permission checks.
- Can manage other admins, roles, and system configuration.
- Can approve or reject sensitive actions that require approval.
- Position: `100`.
- You cannot assign `*` through the role API; the system seeds this role.

### Support admin

- Manages users and support tickets.
- Permissions:

  ```json
  {
    "users.read": true,
    "users.edit": true,
    "users.tier": true,
    "tickets.read": true,
    "users.notes": true
  }
  ```

  Note: This role does not use a `tickets.*` wildcard; assign the specific ticket scopes you need (`tickets.read`, `tickets.respond`, `tickets.close`).

- Position: `60`.

### Moderation admin

- Handles content moderation and user blocking.
- Permissions:

  ```json
  {
    "automod.read": true,
    "reports.read": true,
    "reports.action": true,
    "blocked_users.read": true,
    "blocked_hashes.read": true,
    "admin.moderation.bans": true
  }
  ```

  If you need full moderation families, assign the individual scopes (for example, both `reports.read` and `reports.action` instead of `reports.*`) because wildcard assignment is rejected by the API.

- Position: `60`.

### Read-only admin

- Has read-only access to dashboards and metrics.
- Permissions:

  ```json
  {
    "users.read": true,
    "servers.read": true,
    "metrics.read": true,
    "tickets.read": true
  }
  ```

- Position: `10`.

## Permission scopes

The following scopes are defined in `src/core/admin/permissions.py`. Only these scopes are valid. Scopes such as `users.force_password_change`, `servers.ban`, and `reports.resolve` do not exist — use the scopes listed here instead.

### User management

| Scope | Description |
|-------|-------------|
| `users.read` | View user information and profiles |
| `users.edit` | Edit user profiles and basic settings |
| `users.delete` | Delete user accounts |
| `users.force_purge` | Immediately purge user accounts (dangerous) |
| `users.tier` | Modify user account tiers |
| `users.badges` | Manage user badges |
| `users.notes` | View and edit internal admin notes |
| `users.lock` | Lock or unlock user accounts |
| `users.force_username_change` | Force username changes |

### Server management

| Scope | Description |
|-------|-------------|
| `servers.read` | View server information |
| `servers.edit` | Edit server settings |
| `servers.delete` | Delete servers |
| `servers.transfer` | Transfer server ownership |

There is no `servers.ban` scope. To ban or unban users platform-wide, use `admin.moderation.bans` (see [Platform moderation](#platform-moderation)).

### Moderation

| Scope | Description |
|-------|-------------|
| `automod.*` | Full AutoMod management (internal family) |
| `automod.read` | View AutoMod rules and settings |
| `automod.edit` | Edit AutoMod rules |
| `reports.*` | Full report management (internal family) |
| `reports.read` | View reports |
| `reports.action` | Take action on reports |
| `blocked_users.*` | Full user-blocking management (internal family) |
| `blocked_users.read` | View blocked users |
| `blocked_users.edit` | Block or unblock users |
| `blocked_hashes.*` | Full hash-blocking management (internal family) |
| `blocked_hashes.read` | View blocked hashes |
| `blocked_hashes.edit` | Block or unblock hashes |
| `admin.moderation.bans` | Ban and unban users platform-wide |
| `admin.moderation.hashes` | Write and remove blocked content hashes |
| `admin.moderation.rules` | Create, update, and delete AutoMod rules on any server |
| `admin.moderation.config` | Modify the global AutoMod configuration |

There is no `reports.resolve` scope. To review message, user, or hash reports, use `reports.action` (for example, `POST /hash-reports/{id}/review` and `POST /message-reports/{id}/review` require `reports.action`).

### System and dashboard

| Scope | Description |
|-------|-------------|
| `config.read` | View system configuration |
| `config.edit` | Edit system configuration |
| `config.security` | Edit security-sensitive configuration |
| `metrics.read` | View system metrics and statistics |
| `logs.read` | View system logs |
| `maintenance` | Perform maintenance operations |

### Tickets and support

| Scope | Description |
|-------|-------------|
| `tickets.*` | Full ticket management (internal family) |
| `tickets.read` | View tickets |
| `tickets.respond` | Respond to tickets |
| `tickets.close` | Close tickets |

### Admin management

| Scope | Description |
|-------|-------------|
| `admin.read` | View admin accounts and roles |
| `admin.edit` | Edit admin accounts |
| `admin.roles` | Manage admin roles and permissions (list, create, update, delete, assign) |
| `admin.approvals` | Manage approval workflows |
| `admin.audit.read` | View and export the admin audit log |

### Data and security

| Scope | Description |
|-------|-------------|
| `data_export.read` | View and manage data-export requests |
| `data_export.process` | Approve, deny, and generate data exports |
| `admin.migrations.execute` | Run, apply, and roll back database migrations |
| `admin.data.reindex` | Rebuild the search index (bulk message decryption) |
| `admin.dashboard.tuning` | Tune dashboard limits with hot-reload |
| `admin.license.manage` | Apply and force-revalidate licenses |
| `admin.plexijoin.manage` | Establish and sever cross-instance federation links |
| `admin.artifacts.manage` | Force-delete artifacts and manage retention policies |
| `admin.telemetry.reset` | Reset collected telemetry statistics |
| `admin.security.tokens` | Create, rotate, and revoke permanent API access tokens |
| `admin.security.sessions` | Force logout and lock or unlock user accounts |
| `admin.security.ipblock` | Manage IP blocks platform-wide |
| `admin.security.logout_all` | Invalidate every user session platform-wide |
| `admin.security.usernames` | Manage banned username patterns |
| `*` | Full system access (super admin only; not assignable via the role API) |

## Configure RBAC

Configure RBAC in the `admin_ui.rbac` section:

```yaml
admin_ui:
  rbac:
    enabled: true
    default_role: "super_admin"
```

## Assign roles

### In the admin panel

1.  Go to **Admin management**.
2.  Select the admin account.
3.  Choose the roles to assign.
4.  Save your changes.

### With the API

```bash
# Assign a role to an admin
curl -X POST /api/v1/admin/roles/assign \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"admin_id": 123, "role_id": 2}'

# Revoke a role from an admin
curl -X DELETE /api/v1/admin/roles/123/2 \
  -H "Authorization: Bearer <token>"

# List roles assigned to an admin
curl /api/v1/admin/admins/123/roles \
  -H "Authorization: Bearer <token>"
```

Required permission: `admin.roles`.

## Create and update roles

### Create a role

```bash
curl -X POST /api/v1/admin/roles \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "custom_role",
    "description": "Can view users and servers",
    "permissions": {
      "users.read": true,
      "servers.read": true
    }
  }'
```

Rules:

- `permissions` must be a map of `string → boolean` where each key exists in `PERMISSION_SCOPES`.
- You cannot include `*` or any scope that ends with `.*` (for example, `reports.*` or `automod.*`). To grant report access, include both `reports.read` and `reports.action` explicitly.
- `position` is optional. If omitted, the server sets it to your highest position minus one. You can only set a position lower than your own highest position (`0` to `99`, and less than your maximum).
- Role names must be unique.

To verify available scopes, read `src/core/admin/permissions.py` or list existing roles with `GET /api/v1/admin/roles`.

### Update a role

```bash
curl -X PUT /api/v1/admin/roles/456 \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "description": "Updated description",
    "permissions": {
      "users.read": true,
      "users.edit": true
    }
  }'
```

You cannot modify system roles (`is_system = 1`), and you can only modify roles at a lower position than your own. The same scope validation applies as for creation.

### Delete a role

You can only delete a role at a lower position than your own, and only if no admins are currently assigned to it. System roles cannot be deleted.

## Check permissions

When an admin performs an action, the system does the following:

1.  Retrieves all roles assigned to the admin.
2.  Combines permissions from all roles.
3.  Checks whether the required permission is granted using hierarchy matching.
4.  Returns the authorization result.

## Enforce hierarchy

When you manage other admins or roles, the following rules apply:

- You can assign a role only if you can manage both the target admin and the role. This means your highest position must be strictly higher than the target admin's highest position and strictly higher than the role's position.
- You can modify or delete a role only if its position is lower than your highest position. System roles require position `100`.
- You can toggle an admin's status or delete an admin only if their highest position is lower than yours.
- An admin cannot modify themselves. The API enforces self-deletion prevention.
- To check your position, the system uses the maximum position across all your assigned roles.

Required permission for all hierarchy operations: `admin.roles`.

## Audit admin actions

To review permission changes and sensitive operations, query the audit log. The audit endpoints require `admin.audit.read`:

- `GET /api/v1/admin/audit/logs` — paginated audit entries (filter by `action_family`, `status`, `admin_id`, `target_type`, `date_from`, `date_to`, `page`, `per_page`).
- `GET /api/v1/admin/audit/logs/stats` — today's totals by action family.
- `GET /api/v1/admin/audit/logs/export?format=csv` or `?format=json` — export filtered logs.

The correct path is `/audit/logs`, not `/audit-log`. Do not use `/audit-log`; the server returns `404` for that path.

## Best practices

- **Follow the principle of least privilege.** Assign only the minimum permissions needed.
- **Prefer roles over individual permissions.** Create reusable roles for common functions.
- **Audit regularly.** Review role assignments, permission changes, and audit logs.
- **Separate duties.** Use different roles for different functions.
- **Use temporary access.** Assign elevated roles only for the duration of a task.
- **Respect hierarchy.** Consider role positions when you grant management capabilities.

## Troubleshoot

### An admin cannot access a feature

- Check whether the admin has the exact required scope (for example, `reports.action` rather than `reports.resolve`).
- Verify role assignments and that RBAC is enabled.
- Review hierarchy: the admin who assigned the role must have had a high enough position.

### A permission is not working

- Verify spelling and that the scope exists in `PERMISSION_SCOPES` (see `src/core/admin/permissions.py:42`).
- Check for typos such as `users.force_password_change` (use `users.force_username_change`), `servers.ban` (use `admin.moderation.bans`), or `reports.resolve` (use `reports.action`).
- Confirm the permission was granted as `true` in at least one assigned role. Review `GET /api/v1/admin/admins/{id}/roles`.

### You cannot assign a role

- Verify that the role and admin IDs exist.
- Verify that you have `admin.roles`.
- Check the hierarchy: your highest position must be higher than both the target admin's highest position and the role's position.

### You cannot manage a role

- System roles cannot be modified or deleted.
- Check the `position` column in `admin_roles`. You can only manage roles with a lower position than your own.
- Ensure you are not trying to assign a wildcard scope such as `reports.*` — the API rejects these with `400`.
