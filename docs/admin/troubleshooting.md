# Admin Troubleshooting

This guide helps you troubleshoot common issues with the Plexichat admin panel.

## Authentication Issues

### Cannot Login to Admin Panel

**Symptoms**: Login page loads but authentication fails

**Possible Causes**:
- Incorrect username or password
- Account locked due to rate limiting
- 2FA issues
- Host restriction blocking access
- Session issues

**Solutions**:
1. Verify credentials are correct
2. Check if account is rate-limited (wait for lockout period)
3. Ensure 2FA is working correctly
4. Verify your IP is in allowed hosts
5. Clear browser cache and cookies
6. Try different browser

### 2FA Not Working

**Symptoms**: OTP codes are rejected

**Possible Causes**:
- Device time not synchronized
- Incorrect OTP secret
- Authenticator app issues
- Backup codes needed

**Solutions**:
1. Sync device time with network time
2. Try different authenticator app
3. Use backup codes if available
4. Contact super admin to reset 2FA
5. Re-setup OTP if possible

### Session Expiring Too Quickly

**Symptoms**: Frequent logouts required

**Possible Causes**:
- Session timeout configured too short
- IP address changes
- Browser clearing cookies
- Security settings

**Solutions**:
1. Increase session timeout in configuration
2. Check for IP changes (VPN, network switch)
3. Disable browser cookie clearing for admin domain
4. Review security settings

## Permission Issues

### Access Denied Errors

**Symptoms**: "Permission denied" or "Insufficient permissions" errors

**Possible Causes**:
- Missing required permission
- Role not assigned correctly
- RBAC not enabled
- Approval workflow blocking action

**Solutions**:
1. Check your assigned roles
2. Verify required permissions for action
3. Contact super admin for role changes
4. Check if approval is needed for action
5. Review audit logs for details

### Cannot Access Specific Section

**Symptoms**: Some admin sections are inaccessible

**Possible Causes**:
- Missing specific permission
- Section disabled in configuration
- Feature not available

**Solutions**:
1. Check if section requires specific permission
2. Verify section is enabled in configuration
3. Review role permissions
4. Contact super admin for access

## Performance Issues

### Dashboard Loading Slowly

**Symptoms**: Dashboard takes long time to load

**Possible Causes**:
- Large dataset
- Database performance issues
- Network latency
- Server resource constraints

**Solutions**:
1. Check database performance
2. Review system resources
3. Check network connectivity
4. Reduce data range/time period
5. Consider database optimization

### Charts Not Updating

**Symptoms**: Dashboard charts show stale data

**Possible Causes**:
- JavaScript errors
- API connectivity issues
- Browser caching
- WebSocket issues

**Solutions**:
1. Refresh the page
2. Check browser console for errors
3. Clear browser cache
4. Verify API connectivity
5. Check WebSocket connection

## Data Issues

### User Not Found

**Symptoms**: Cannot find user in search

**Possible Causes**:
- User deleted
- Incorrect search criteria
- User ID typo
- Database sync issues

**Solutions**:
1. Verify search criteria
2. Check if user was deleted
3. Try different search terms
4. Check database integrity
5. Review audit logs

### Inconsistent Data Display

**Symptoms**: Data shows different values in different places

**Possible Causes**:
- Caching issues
- Database replication lag
- Concurrent modifications
- Display bugs

**Solutions**:
1. Refresh the page
2. Clear cache
3. Check database consistency
4. Verify data source
5. Report as bug if persistent

## Configuration Issues

### Settings Not Saving

**Symptoms**: Configuration changes not persisting

**Possible Causes**:
- File permission issues
- Database write errors
- Configuration validation errors
- Concurrent modifications

**Solutions**:
1. Check file permissions
2. Verify database connectivity
3. Review configuration validation
4. Check for error messages
5. Try again after short delay

### Changes Not Taking Effect

**Symptoms**: Configuration changes applied but not working

**Possible Causes**:
- Service restart required
- Caching issues
- Incorrect configuration
- Dependency issues

**Solutions**:
1. Restart the service
2. Clear cache
3. Verify configuration syntax
4. Check for conflicting settings
5. Review documentation

## Audit Logging Issues

### Actions Not Being Logged

**Symptoms**: Some admin actions not appearing in audit log

**Possible Causes**:
- Audit logging disabled
- Database connection issues
- File permission issues
- Logging configuration errors

**Solutions**:
1. Verify audit logging is enabled
2. Check database connectivity
3. Review file permissions
4. Check logging configuration
5. Review error logs

### Audit Logs Missing Details

**Symptoms**: Audit log entries incomplete

**Possible Causes**:
- Logging level too low
- Data sanitization
- Configuration issues
- Database schema issues

**Solutions**:
1. Check logging level configuration
2. Review data sanitization settings
3. Verify database schema
4. Check for logging errors
5. Update logging configuration

## Approval Workflow Issues

### Approval Requests Not Created

**Symptoms**: Sensitive actions execute without approval

**Possible Causes**:
- Approval workflows disabled
- Action not in approval list
- Single admin bypass active
- Permission issues

**Solutions**:
1. Verify approval workflows enabled
2. Check action is in require_approval_for list
3. Review single_admin_bypass setting
4. Check admin permissions
5. Review configuration

### Approvals Not Counting

**Symptoms**: Approvals given but not counted

**Possible Causes**:
- Already approved by same admin
- Request expired
- Permission issues
- Database issues

**Solutions**:
1. Check if you already approved
2. Verify request is not expired
3. Check approval permissions
4. Review database state
5. Check audit logs

## System Issues

### Service Not Starting

**Symptoms**: Admin panel service fails to start

**Possible Causes**:
- Configuration errors
- Database connection issues
- Port conflicts
- Resource constraints

**Solutions**:
1. Check configuration syntax
2. Verify database connectivity
3. Check for port conflicts
4. Review system resources
5. Check error logs

### High Memory Usage

**Symptoms**: Admin panel consuming excessive memory

**Possible Causes**:
- Memory leaks
- Large datasets
- Caching issues
- Configuration problems

**Solutions**:
1. Review memory usage patterns
2. Reduce data cache size
3. Check for memory leaks
4. Optimize configuration
5. Restart service

## Getting Help

### When to Contact Support

- Issues not resolved by troubleshooting
- Security concerns
- Data integrity issues
- System outages
- Configuration assistance

### Information to Provide

When seeking help, provide:
- Plexichat version
- Error messages (exact text)
- Steps to reproduce
- Configuration details
- Audit log excerpts
- System logs

### Diagnostic Information

Collect diagnostic information:
```bash
# System information
uname -a
python --version

# Plexichat version
curl http://localhost:8000/api/v1/version

# Configuration check
# (review your config file)

# Recent logs
# (check application logs)
```

## Prevention

### Regular Maintenance

- Review system logs weekly
- Check disk space monthly
- Update software regularly
- Review audit logs monthly
- Test backup restoration quarterly

### Monitoring

- Set up alerting for critical errors
- Monitor system resources
- Track failed login attempts
- Monitor database performance
- Review approval workflow status

### Documentation

- Document custom configurations
- Keep change logs
- Maintain runbooks
- Document troubleshooting steps
- Update contact information