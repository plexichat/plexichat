# Audit Logging

Plexichat provides comprehensive audit logging for all admin actions, supporting both file-based and database logging for complete audit trails and compliance requirements.

## Overview

The audit logging system captures:
- All admin authentication events (login, logout, failed attempts)
- User management actions (create, edit, delete, lock)
- Server management actions
- Configuration changes
- Role assignments and permission changes
- Approval workflow actions
- System operations

## Configuration

Audit logging is configured in the `admin_ui.audit` section:

```yaml
admin_ui:
  audit:
    log_to_file: true
    log_to_database: true
    sensitive_actions_always_db: true
    retention_days: 365
```

### Configuration Options

- `log_to_file`: Enable logging to file system logs
- `log_to_database`: Enable logging to admin_audit_log database table
- `sensitive_actions_always_db`: Always log sensitive actions to database regardless of config
- `retention_days`: Number of days to retain audit logs (for database cleanup)

## Database Schema

The `admin_audit_log` table stores audit entries:

```sql
CREATE TABLE admin_audit_log (
    id INTEGER PRIMARY KEY,
    admin_id INTEGER NOT NULL,
    action VARCHAR(100) NOT NULL,
    target_type VARCHAR(50),
    target_id INTEGER,
    details TEXT,
    ip_address VARCHAR(45),
    user_agent TEXT,
    status VARCHAR(20) NOT NULL DEFAULT 'success',
    created_at INTEGER NOT NULL
)
```

## Logged Actions

### Authentication
- `login` - Successful admin login
- `login_failed` - Failed login attempt
- `logout` - Admin logout
- `otp_setup` - OTP 2FA setup
- `otp_verify` - OTP verification
- `password_change` - Password change

### User Management
- `user.create` - User account creation
- `user.edit` - User profile edit
- `user.delete` - User account deletion
- `user.force_purge` - Immediate user purge
- `user.lock` - User account lock
- `user.unlock` - User account unlock
- `user.tier_change` - User tier modification
- `user.badge_add` - Badge assignment
- `user.badge_remove` - Badge removal

### Server Management
- `server.create` - Server creation
- `server.edit` - Server settings edit
- `server.delete` - Server deletion
- `server.ban` - User ban from server

### Admin Management
- `admin.create` - Admin account creation
- `admin.edit` - Admin account edit
- `admin.delete` - Admin account deletion
- `role.assign` - Role assignment
- `role.revoke` - Role revocation
- `force_password_change` - Forced password change

### System Operations
- `config.modify` - Configuration changes
- `migration.run` - Database migration execution
- `system.restart` - System restart

## Viewing Audit Logs

### Via Admin Panel
1. Navigate to the Audit Log section
2. Filter by date range, admin, action type, or status
3. View detailed information for each entry
4. Export logs for compliance reporting

### Via Database Query
```sql
SELECT 
    id,
    admin_id,
    action,
    target_type,
    target_id,
    details,
    ip_address,
    status,
    datetime(created_at/1000, 'unixepoch') as created_at
FROM admin_audit_log
WHERE created_at > ?
ORDER BY created_at DESC
LIMIT 100;
```

### Via API
```bash
GET /api/v1/admin/audit-log?admin_id=123&action=user.delete&hours=24
```

## Log Retention

### File Logs
File logs are managed by the system logger:
- Automatic rotation based on file size
- Compression of old logs
- Configurable retention period

### Database Logs
Database logs can be cleaned up based on retention policy:
```sql
DELETE FROM admin_audit_log 
WHERE created_at < ?
```

The `?` parameter should be the timestamp for `retention_days` ago.

## Compliance Features

### Immutable Logs
- Audit logs cannot be modified by admins
- Database logs are protected by permission system
- File logs are written with append-only access

### Complete Trail
- All sensitive actions are logged
- IP addresses and user agents captured
- Timestamps with millisecond precision
- Success/failure status tracking

### Export Capabilities
- CSV export for analysis
- JSON export for integration
- PDF export for reporting

## Security Considerations

1. **Log Access**: Restrict audit log access to authorized admins only
2. **Log Integrity**: Use write-once storage for critical logs
3. **Log Backup**: Regular backups of audit logs
4. **Log Monitoring**: Alert on suspicious patterns in logs
5. **Log Retention**: Follow regulatory requirements for retention

## Best Practices

1. **Enable Dual Logging**: Use both file and database logging for redundancy
2. **Regular Review**: Schedule regular audit log reviews
3. **Alert Configuration**: Set up alerts for critical actions
4. **Backup Strategy**: Implement regular backup of audit logs
5. **Access Control**: Limit who can view and manage audit logs

## Troubleshooting

### Logs Not Appearing
- Check if audit logging is enabled in configuration
- Verify database connection is working
- Check file system permissions for log directory

### Missing Sensitive Actions
- Ensure `sensitive_actions_always_db` is enabled
- Check that the action is in the sensitive actions list
- Verify the admin has appropriate permissions

### Database Growing Too Large
- Review and adjust `retention_days` setting
- Implement regular cleanup jobs
- Consider archiving old logs to separate storage

### Performance Impact
- Monitor database performance with high log volume
- Consider indexing strategy for audit_log table
- Evaluate async logging for high-traffic systems