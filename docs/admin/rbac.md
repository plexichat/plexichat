# Role-Based Access Control (RBAC)

Plexichat's RBAC system provides granular control over admin permissions through role assignments and permission scopes.

## Overview

The RBAC system allows you to:
- Define custom admin roles with specific permissions
- Assign multiple roles to individual admins
- Control access to sensitive operations
- Implement the principle of least privilege

## Permission Model

Permissions are checked via **wildcard-hierarchy matching**. A permission scope like `users.*` grants access to all sub-permissions (`users.read`, `users.edit`, `users.delete`). The wildcard `*` alone grants full super-admin access.

Permission checking follows this order:
1. If a role has `"*": true`, all checks pass immediately (super admin)
2. Exact match is checked first (e.g., `users.read`)
3. Parent wildcard match is checked (e.g., `users.*` grants `users.read`, `users.edit`)
4. Top-level wildcard `*` grants everything
5. Explicit `false` values deny only that specific scope (not its sub-scopes)

## Admin Role Hierarchy

Admin roles have a **position** value that enforces hierarchy. Admins can only manage roles and admins at a **lower position** than their own highest role position.

| Position | Implied Authority |
|----------|-------------------|
| 100      | Super Admin (highest) |
| 80       | Senior Admin |
| 60       | Standard Admin |
| 40       | Junior Admin |
| 10       | Support / Read-Only (lowest) |

System roles (created by the system) have fixed high positions and cannot be deleted or demoted.

## Default Roles

### Super Admin
- **Full system access** with all permissions
- Can manage other admins and roles
- Can modify system configuration
- Can approve/reject sensitive actions
- **Permission**: `{"*": true}`
- **Position**: 100

### Support Admin
- User management and support access
- Can view and edit user profiles
- Can manage user tiers and badges
- Can view and add internal notes
- **Permissions**:
  ```json
  {
    "users.read": true,
    "users.edit": true,
    "users.tier": true,
    "tickets.*": true,
    "users.notes": true
  }
  ```
- **Position**: 60

### Moderation Admin
- Content moderation and user blocking
- Can manage automod rules
- Can review and handle reports
- Can block users and content
- **Permissions**:
  ```json
  {
    "automod.*": true,
    "reports.*": true,
    "blocked_users.*": true,
    "blocked_hashes.*": true
  }
  ```
- **Position**: 60

### Read-Only Admin
- Read-only access to dashboard and metrics
- Can view user and server information
- Cannot make changes
- **Permissions**:
  ```json
  {
    "*": false,
    "users.read": true,
    "servers.read": true,
    "metrics.read": true,
    "tickets.read": true
  }
  ```
- **Position**: 10

## Permission Scopes

### User Management
| Scope | Description |
|-------|-------------|
| `users.read` | View user information |
| `users.edit` | Edit user profiles |
| `users.delete` | Delete user accounts |
| `users.force_purge` | Immediately purge accounts (dangerous) |
| `users.tier` | Modify user account tiers |
| `users.badges` | Manage user badges |
| `users.notes` | View/edit internal admin notes |
| `users.lock` | Lock/unlock user accounts |
| `users.force_username_change` | Force username changes |
| `users.force_password_change` | Force password changes |



### Server Management
| Scope | Description |
|-------|-------------|
| `servers.read` | View server information |
| `servers.edit` | Edit server settings |
| `servers.delete` | Delete servers |

### Moderation
| Scope | Description |
|-------|-------------|
| `automod.*` | Full automod access |
| `automod.read` | View automod rules |
| `automod.edit` | Edit automod rules |
| `reports.*` | Full report access |
| `reports.read` | View reports |
| `reports.resolve` | Resolve reports |
| `blocked_users.*` | Full blocked user access |
| `blocked_hashes.*` | Full blocked hash access |

### System & Dashboard
| Scope | Description |
|-------|-------------|
| `config.read` | View system configuration |
| `config.edit` | Edit system configuration |
| `metrics.read` | View system metrics |
| `logs.read` | View system logs |

### Admin Management
| Scope | Description |
|-------|-------------|
| `admin.users` | Manage admin user accounts (create/delete/toggle) |
| `admin.read` | View admin accounts |
| `admin.edit` | Edit admin accounts |
| `admin.roles` | Full role management (list, create, update, delete, assign) |
| `admin.roles.read` | View roles |
| `admin.roles.assign` | Assign/revoke roles to/from admins |
| `admin.approvals` | Manage approval workflows |

## Configuration

RBAC is configured in the `admin_ui.rbac` section of your configuration:

```yaml
admin_ui:
  rbac:
    enabled: true
    default_role: "super_admin"
```

## Role Assignment

Roles are assigned through the admin panel or API:

### Via Admin Panel
1. Navigate to Admin Management
2. Select the admin account
3. Choose roles from the available roles
4. Save changes

### Via API
```bash
# Assign a role to an admin
POST /api/v1/admin/roles/assign
{
  "admin_id": 123,
  "role_id": 2
}

# Revoke a role from an admin
DELETE /api/v1/admin/roles/{admin_id}/{role_id}
```

## Custom Roles

Custom roles can be created with specific permission sets:

### Via API
```bash
POST /api/v1/admin/roles
{
  "name": "custom_role",
  "description": "Custom role with specific permissions",
  "permissions": {
    "users.read": true,
    "servers.read": true
  }
}
```

## Permission Checking

Permissions are checked automatically when admins perform actions. The system:

1. Retrieves all roles assigned to the admin
2. Combines permissions from all roles (using wildcard-hierarchy matching)
3. Checks if the required permission is granted
4. Returns authorization result

## Admin Role Hierarchy Enforcement

When managing other admins or editing roles:

1. An admin can only **assign roles** at or below their own highest role position
2. An admin can only **modify or delete roles** at a lower position than their own
3. An admin can only **toggle status or delete** an admin user if their highest role position is lower
4. Super admins (position 100) can manage all roles and admins
5. Admins cannot modify themselves (self-deletion prevention is enforced at the API level)

Hierarchy checks use the admin's **maximum role position** across all assigned roles. An admin with multiple roles uses the highest position for hierarchy enforcement.

## Best Practices

1. **Principle of Least Privilege**: Assign only the minimum permissions needed
2. **Role-Based**: Use roles rather than individual permissions where possible
3. **Regular Audits**: Review role assignments regularly
4. **Separation of Duties**: Use different roles for different functions
5. **Temporary Access**: Use temporary role assignments for special tasks
6. **Hierarchy Awareness**: Be mindful of role positions when assigning management capabilities

## Troubleshooting

### Admin Cannot Access Feature
- Check if the admin has the required permission
- Verify role assignments are correct
- Ensure RBAC is enabled in configuration

### Permission Not Working
- Check permission spelling matches exactly
- Verify the permission scope exists
- Check for wildcard precedence (specific grants override general denies)

### Role Assignment Issues
- Ensure the role exists in the database
- Check that the admin ID is valid
- Verify the assigning admin has permission to assign roles
- Check role hierarchy: the assigning admin must have a position higher than the target

### Hierarchy Enforcement
- Ensure target admin's highest role position is lower than the current admin's highest position
- System roles (is_system = 1) have fixed positions and cannot be modified or deleted
- Check the `position` column in `admin_roles` for current values
