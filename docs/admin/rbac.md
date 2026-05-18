# Role-Based Access Control (RBAC)

Plexichat's RBAC system provides granular control over admin permissions through role assignments and permission scopes.

## Overview

The RBAC system allows you to:
- Define custom admin roles with specific permissions
- Assign multiple roles to individual admins
- Control access to sensitive operations
- Implement the principle of least privilege

## Default Roles

### Super Admin
- **Full system access** with all permissions
- Can manage other admins and roles
- Can modify system configuration
- Can approve/reject sensitive actions
- **Permission**: `{"*": true}`

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

## Permission Scopes

### User Management
- `users.read` - View user information
- `users.edit` - Edit user profiles
- `users.delete` - Delete user accounts
- `users.force_purge` - Immediately purge accounts (dangerous)
- `users.tier` - Modify user account tiers
- `users.badges` - Manage user badges
- `users.notes` - View/edit internal admin notes
- `users.lock` - Lock/unlock user accounts
- `users.force_username_change` - Force username changes
- `users.force_password_change` - Force password changes

### Server Management
- `servers.read` - View server information
- `servers.edit` - Edit server settings
- `servers.delete` - Delete servers
- `servers.ban` - Ban users from servers

### Moderation
- `automod.*` - Full automod access
- `automod.read` - View automod rules
- `automod.edit` - Edit automod rules
- `reports.*` - Full report access
- `reports.read` - View reports
- `reports.resolve` - Resolve reports
- `blocked_users.*` - Full blocked user access
- `blocked_hashes.*` - Full blocked hash access

### System
- `config.read` - View system configuration
- `config.edit` - Edit system configuration
- `metrics.read` - View system metrics
- `logs.read` - View system logs
- `migrations.run` - Run database migrations

### Admin Management
- `admin.read` - View admin accounts
- `admin.edit` - Edit admin accounts
- `admin.delete` - Delete admin accounts
- `admin.roles.*` - Full role management
- `admin.roles.read` - View roles
- `admin.roles.edit` - Edit roles
- `admin.roles.assign` - Assign roles to admins

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
POST /api/v1/admin/roles/assign
{
  "admin_id": 123,
  "role_id": 2
}
```

## Custom Roles

You can create custom roles with specific permission sets:

### Via Database
```sql
INSERT INTO admin_roles (name, description, permissions, created_at, created_by, is_system)
VALUES (
  'custom_role',
  'Custom role description',
  '{"users.read": true, "servers.read": true}',
  1234567890,
  1,
  0
);
```

### Via API (when endpoints are implemented)
```bash
POST /api/v1/admin/roles
{
  "name": "custom_role",
  "description": "Custom role description",
  "permissions": {
    "users.read": true,
    "servers.read": true
  }
}
```

## Permission Checking

Permissions are checked automatically when admins perform actions. The system:

1. Retrieves all roles assigned to the admin
2. Combines permissions from all roles
3. Checks if the required permission is granted
4. Returns authorization result

## Best Practices

1. **Principle of Least Privilege**: Assign only the minimum permissions needed
2. **Role-Based**: Use roles rather than individual permissions where possible
3. **Regular Audits**: Review role assignments regularly
4. **Separation of Duties**: Use different roles for different functions
5. **Temporary Access**: Use temporary role assignments for special tasks

## Troubleshooting

### Admin Cannot Access Feature
- Check if the admin has the required permission
- Verify role assignments are correct
- Ensure RBAC is enabled in configuration

### Permission Not Working
- Check permission spelling matches exactly
- Verify the permission scope exists
- Check for conflicting permissions (deny vs allow)

### Role Assignment Issues
- Ensure the role exists in the database
- Check that the admin ID is valid
- Verify the assigning admin has permission to assign roles