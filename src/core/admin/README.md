# Admin

Manage server administration, role-based access control (RBAC), and approval workflows for Plexichat.

## Overview

The admin module provides server administration features such as admin user management, role and permission management, approval workflows, system metrics, security controls, moderation tools, and database migration management. You initialize the module once during server startup and then call its public API throughout the codebase.

The module enforces RBAC through roles that contain permission mappings. It also enforces position-based hierarchy for management operations and writes every administrative action to a dual file and database audit log.

## Before you begin

Ensure you have:

* A connected `Database` instance.
* An `auth` module if you use password and session features.
* A configuration dictionary if you use approval workflows or audit logging.

## Role-based access control

### Permission scopes

Permissions are dot-separated strings that are case-sensitive. The table lists all scopes that are defined in `permissions.py:42`.

| Scope | Description |
|---|---|
| `users.read` | View user information and profiles |
| `users.edit` | Edit user profiles and basic settings |
| `users.delete` | Delete user accounts |
| `users.force_purge` | Immediately purge user accounts |
| `users.tier` | Modify user account tiers |
| `users.badges` | Manage user badges |
| `users.notes` | View and edit internal admin notes |
| `users.lock` | Lock and unlock user accounts |
| `users.force_username_change` | Force username changes |
| `servers.read` | View server information |
| `servers.edit` | Edit server settings |
| `servers.delete` | Delete servers |
| `servers.transfer` | Transfer server ownership |
| `automod.read` | View AutoMod rules and settings |
| `automod.edit` | Edit AutoMod rules |
| `automod.*` | Full AutoMod management (covers `automod.read` and `automod.edit`) |
| `reports.read` | View reports |
| `reports.action` | Take action on reports |
| `reports.*` | Full report management (covers `reports.read` and `reports.action`) |
| `blocked_users.read` | View blocked users |
| `blocked_users.edit` | Block and unblock users |
| `blocked_users.*` | Full user blocking management |
| `blocked_hashes.read` | View blocked hashes |
| `blocked_hashes.edit` | Block and unblock hashes |
| `blocked_hashes.*` | Full hash blocking management |
| `metrics.read` | View system metrics and statistics |
| `config.read` | View system configuration |
| `config.edit` | Edit system configuration |
| `config.security` | Edit security-sensitive configuration |
| `logs.read` | View system logs |
| `maintenance` | Perform maintenance operations |
| `tickets.read` | View tickets |
| `tickets.respond` | Respond to tickets |
| `tickets.close` | Close tickets |
| `tickets.*` | Full ticket management (covers all `tickets.` permissions) |
| `admin.read` | View admin accounts and roles |
| `admin.edit` | Edit admin accounts |
| `admin.roles` | Manage admin roles and permissions |
| `admin.approvals` | Handle approval workflows |
| `data_export.read` | View and manage data export requests |
| `data_export.process` | Approve, deny, and generate data exports |
| `admin.audit.read` | View and export the admin audit log |
| `admin.migrations.execute` | Run, apply, and roll back database migrations |
| `admin.security.ipblock` | Block and unblock IP addresses platform-wide |
| `admin.security.tokens` | Create, rotate, and revoke permanent API access tokens |
| `admin.security.sessions` | Force logout and lock or unlock user accounts |
| `admin.security.logout_all` | Invalidate every user session platform-wide |
| `admin.security.usernames` | Manage banned username patterns |
| `admin.moderation.bans` | Ban and unban users platform-wide |
| `admin.moderation.hashes` | Write and remove blocked content hashes |
| `admin.moderation.rules` | Create, update, and delete AutoMod rules on any server |
| `admin.moderation.config` | Modify the global AutoMod configuration |
| `admin.data.reindex` | Rebuild the search index |
| `admin.artifacts.manage` | Force-delete artifacts and manage retention policies |
| `admin.dashboard.tuning` | Hot-reload cross-worker poll-loop tuning constants |
| `admin.license.manage` | Apply and force-revalidate licenses |
| `admin.plexijoin.manage` | Establish and sever cross-instance federation links |
| `admin.telemetry.reset` | Reset collected telemetry statistics |
| `*` | Full system access (super admin only) |

### Wildcard resolution

`check_permission` in `permissions.py:139` evaluates permissions in the following order for each role assigned to the admin:

1. If the role grants `"*": true`, the check succeeds immediately. This is the super-admin grant. Only grant this scope to roles that require full system access.
2. If the required permission contains a dot, the check derives a one-level wildcard prefix by taking the first segment before the dot and appending `.*`. For example, a check for `tickets.read` looks for `tickets.*`. If the role grants that prefix, the check succeeds. This covers shallow groups such as `automod.*`, `reports.*`, `blocked_users.*`, `blocked_hashes.*`, and `tickets.*`.
3. Otherwise, the check looks for an exact match of the required permission string.

The wildcard check is one level deep. A permission such as `admin.moderation.bans` does not match `admin.*` (which is not a defined scope except for `*`). To grant deep administrative permissions, grant each explicit scope or grant `*`.

### Hierarchy and management rules

The module enforces position-based hierarchy in `permissions.py:369`:

* `get_admin_max_position` returns the highest `position` value across all roles assigned to an admin. System roles require `position >= 100`.
* `can_manage_admin` returns `true` only when the acting admin's maximum position is strictly greater than the target admin's maximum position. It returns `false` for self-management.
* `can_manage_role` returns `true` only when the acting admin's maximum position is strictly greater than the role's position, or `>= 100` for system roles.

Assign higher `position` values to more privileged roles and keep the super-admin role at `position >= 100`.

### Approval workflows

Actions that are listed in `APPROVAL_REQUIRED_ACTIONS` in `permissions.py:122` require approval when approval workflows are enabled:

`users.force_purge`, `users.delete`, `servers.delete`, `config.security`, `admin.roles`, `migrations.run`, `migrations.rollback`, `migrations.apply_all`, `migrations.emergency_override`, `search.reindex`, `artifacts.force_delete`.

`check_approval_required` in `permissions.py:213` applies the following logic:

* If `approval_workflows.enabled` is `false`, the action is allowed without approval.
* If `approval_workflows.single_admin_bypass` is `true` and exactly one row exists in `admin_users`, the action bypasses approval.
* If the admin holds the `*` permission, the action bypasses approval.
* Otherwise, the module creates a row in `admin_approvals` and returns `requires_approval: true` with the `approval_id`.

## Set up the module

Initialize the module once in your server entry point:

```python
from src.core import admin

admin.setup(db, auth_module, features_module)
auth.ensure_admin_user(db)
```

To check whether the module is initialized, call `admin.is_setup()`.

## Use the module

### Check a permission

```python
from src.core.admin.permissions import check_permission, check_admin_permission

result = check_permission(admin_id, "tickets.read", db)
if not result.has_permission:
    raise PermissionError(result.reason)

# Boolean convenience wrapper
if check_admin_permission(admin_id, "users.tier", db):
    # Allow the operation
    pass
```

### Manage tickets

```python
from src.core import admin

tickets = admin.get_feedback_tickets(status_filter="open", limit=50)
admin.update_ticket_status(ticket_id, "resolved", admin_user_id)
admin.add_internal_note(ticket_id, admin_user_id, "Contacted user via email")
```

### Check host restriction

```python
from src.core import admin

allowed_hosts = ["127.0.0.1", "localhost", "10.0.0.0/24"]
if admin.check_host_restriction(client_ip, allowed_hosts):
    # Allow access to /admin
    pass
```

`check_host_restriction` in `__init__.py:119` handles localhost variants, CIDR notation, and exact IP matches. An empty `allowed_hosts` list allows all hosts.

### Log an administrative action

```python
from src.core.admin.permissions import log_admin_action

log_admin_action(
    db,
    admin_id=actor_id,
    action="users.tier",
    target_type="user",
    target_id=user_id,
    details={"tier": "premium"},
    ip_address=client_ip,
    status="success",
)
```

The logger writes to both file and database when `admin_ui.audit.log_to_file` and `admin_ui.audit.log_to_database` are enabled. Sensitive actions such as `users.force_purge` always write to the database.

## Configuration

Add the following keys to `config.yaml`:

```yaml
admin_ui:
  enabled: true
  path: /admin
  host_restriction:
    enabled: true
    allowed_hosts:
      - 127.0.0.1
      - localhost
  require_admin_role: true
  audit:
    log_to_file: true
    log_to_database: true
  approval_workflows:
    enabled: false
    single_admin_bypass: true
    approval_required_admins: 2
    approval_timeout_hours: 48
    require_approval_for:
      - users.delete
      - servers.delete
```

## API endpoints

| Method and path | Required permission | Description |
|---|---|---|
| `GET /api/v1/admin/users` | `admin.read` | List admin users |
| `POST /api/v1/admin/users` | `admin.edit` | Create an admin user |
| `PUT /api/v1/admin/users/{id}` | `admin.edit` and hierarchy check | Update an admin user |
| `DELETE /api/v1/admin/users/{id}` | `admin.edit` and hierarchy check | Delete an admin user |
| `POST /api/v1/admin/users/{id}/toggle-status` | `admin.edit` and hierarchy check | Enable or disable an admin user |
| `GET /api/v1/admin/migrations` | `admin.migrations.execute` or `*` | List migrations |
| `GET /api/v1/admin/migrations/{version}` | `admin.migrations.execute` or `*` | Get migration details |
| `POST /api/v1/admin/migrations/{version}/run` | `admin.migrations.execute` and approval | Run a migration |
| `POST /api/v1/admin/migrations/emergency-override` | `admin.migrations.execute` and approval | Generate an emergency bypass token |

Management endpoints that modify another admin or a role also enforce the position hierarchy described in the hierarchy section.

## Database schema

The module adds an `is_admin` column to `auth_users` and creates tables such as `admin_roles`, `admin_role_assignments`, `admin_approvals`, `admin_audit_log`, and `admin_notes`. See `schema.py` for the complete definitions.

Example audit table:

```sql
CREATE TABLE admin_audit_log (
    id INTEGER PRIMARY KEY,
    admin_id INTEGER NOT NULL,
    action TEXT NOT NULL,
    target_type TEXT,
    target_id INTEGER,
    details TEXT,
    ip_address TEXT,
    user_agent TEXT,
    status TEXT NOT NULL,
    created_at INTEGER NOT NULL
);
```

Also adds columns to the `feedback` table: `status`, `resolved_at`, `resolved_by`, and `internal_notes`.

## Frontend structure

The dashboard is split into `dashboard.html`, `dashboard.js`, and `dashboard.css`. Tabs include Metrics, Tickets, Users, Deletions, Security, AutoMod, Logs, Roles, Approvals, and Migrations.
