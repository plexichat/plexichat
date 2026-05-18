# Getting Started with Plexichat Admin

This guide helps you get started with administering a Plexichat deployment.

## Initial Setup

### 1. Access the Admin Panel

The admin panel is typically accessible at:
```
https://your-domain.com/admin
```

Or your configured admin path from settings.

### 2. Initial Login

On first login:
1. Use the initial admin credentials provided during setup
2. You'll be prompted to set up Two-Factor Authentication (2FA)
3. Complete OTP setup using your authenticator app
4. Store your backup codes securely

### 3. Configure Basic Settings

Navigate to Settings and configure:

#### Security Settings
- Enable/disable 2FA requirement
- Set password policies
- Configure session timeouts
- Set up host restrictions

#### RBAC Settings
- Enable/disable RBAC
- Set default admin role
- Configure approval workflows

#### Audit Settings
- Enable file/database logging
- Set log retention period
- Configure sensitive action logging

## First-Time Configuration

### Enable RBAC

1. Go to Settings -> RBAC
2. Enable RBAC system
3. Review default roles
4. Create custom roles if needed
5. Assign appropriate roles to admins

### Configure Approval Workflows

1. Go to Settings -> Approval Workflows
2. Enable approval workflows
3. Select actions requiring approval
4. Set approval requirements
5. Configure timeout settings

### Set Up Audit Logging

1. Go to Settings -> Audit Logging
2. Enable dual logging (file + database)
3. Set retention period
4. Configure sensitive action logging
5. Test logging functionality

## Common First Tasks

### Create Additional Admins

1. Navigate to Admin Management
2. Click "Create Admin"
3. Fill in admin details
4. Assign appropriate role
5. Admin will need to set up 2FA on first login

### Review System Status

1. Check Dashboard overview
2. Review system metrics
3. Check for any alerts
4. Verify database status
5. Review recent activity

### Configure Notifications

1. Go to Settings -> Notifications
2. Configure email settings
3. Set up webhook notifications
4. Configure alert thresholds
5. Test notification delivery

## Understanding the Dashboard

### Overview Section
- Active users count
- Total users count
- Scheduled deletions
- System performance metrics

### Metrics Section
- Real-time performance charts
- Historical data trends
- Endpoint performance
- Database query statistics

### User Management
- User search and filtering
- User profile editing
- Account status management
- Internal notes system

### Server Management
- Server overview
- Server configuration
- Member management
- Server moderation

## Basic Operations

### Managing Users

#### View User Profile
1. Go to User Management
2. Search for user by ID or username
3. Click on user to view profile
4. Review user information and activity

#### Edit User Profile
1. Open user profile
2. Click "Edit Profile"
3. Make necessary changes
4. Save changes
5. Action is logged to audit trail

#### Lock User Account
1. Open user profile
2. Click "Lock Account"
3. Provide reason for lock
4. Confirm action
5. User cannot login while locked

### Managing Servers

#### View Server Details
1. Go to Server Management
2. Select server from list
3. Review server information
4. Check member count and activity

#### Edit Server Settings
1. Open server details
2. Click "Edit Settings"
3. Modify server configuration
4. Save changes
5. Changes take effect immediately

## Security Best Practices

### Immediate Actions
1. Enable 2FA for all admin accounts
2. Set strong password policies
3. Configure host restrictions
4. Enable audit logging
5. Review default admin credentials

### Ongoing Practices
1. Regularly review audit logs
2. Monitor failed login attempts
3. Keep software updated
4. Review admin access regularly
5. Backup configuration and data

## Troubleshooting Common Issues

### Cannot Access Admin Panel
- Check if admin panel is enabled in configuration
- Verify host restrictions allow your IP
- Check if you're using the correct URL
- Clear browser cache and cookies
- Try different browser

### 2FA Setup Issues
- Ensure your device time is synchronized
- Try different authenticator app
- Verify you're entering the correct code
- Use backup codes if needed
- Contact system administrator if issues persist

### Permission Denied Errors
- Check your assigned roles
- Verify you have required permissions
- Contact super admin for role changes
- Check if approval workflow is blocking action
- Review audit logs for details

## Next Steps

1. **Complete Initial Setup** - Finish all configuration steps
2. **Create Admin Accounts** - Set up additional admins as needed
3. **Configure Roles** - Create custom roles for your organization
4. **Set Up Monitoring** - Configure alerts and notifications
5. **Review Documentation** - Read detailed guides for specific features

## Getting Help

- **Documentation**: Check the [Admin Documentation](index.md) for detailed guides
- **Audit Logs**: Review audit logs for error details
- **System Logs**: Check system logs for technical issues
- **Support**: Contact your system administrator or support team

## Configuration Reference

### Minimum Recommended Configuration

```yaml
admin_ui:
  enabled: true
  require_otp: true
  force_password_change_first_login: true
  rbac:
    enabled: true
    default_role: "support_admin"
  approval_workflows:
    enabled: true
    require_approval_for:
      - users.force_purge
      - users.delete
  audit:
    log_to_file: true
    log_to_database: true
    retention_days: 365
  security:
    password_policy:
      min_length: 12
      require_uppercase: true
      require_lowercase: true
      require_numbers: true
      require_special_chars: true
```