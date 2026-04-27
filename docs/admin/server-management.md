# Server Management

This guide covers server administration operations in the Plexichat admin panel.

## Overview

Server management allows admins to:
- View and manage servers
- Configure server settings
- Manage server members
- Handle server moderation
- Monitor server activity

## Viewing Servers

### Server List

Navigate to Server Management to view all servers:
- Server name and ID
- Member count
- Owner information
- Creation date
- Activity status

### Server Details

Click on any server to view:
- Basic server information
- Member list and roles
- Server settings
- Activity metrics
- Moderation history

## Server Operations

### Editing Server Settings

1. Open server details
2. Click "Edit Settings"
3. Modify server configuration
4. Save changes
5. Changes take effect immediately

**Required Permissions**: `servers.edit`

### Deleting Servers

**WARNING: DANGEROUS OPERATION** - This cannot be undone!

1. Open server details
2. Click "Delete Server"
3. Provide reason
4. Confirm action
5. Server and all data are deleted

**Required Permissions**: `servers.delete` (typically requires approval)

## Member Management

### Viewing Members

Server member list shows:
- Member username and ID
- Member roles
- Join date
- Activity status

### Managing Member Roles

1. Open server details
2. Go to Members tab
3. Select member
4. Click "Edit Roles"
5. Add/remove roles
6. Save changes

**Required Permissions**: `servers.edit`

### Banning Members

1. Open server details
2. Go to Members tab
3. Select member
4. Click "Ban"
5. Provide reason and duration
6. Confirm action
7. Member is banned from server

**Required Permissions**: `servers.ban`

### Unbanning Members

1. Open server details
2. Go to Members tab
3. Select banned member
4. Click "Unban"
5. Confirm action
6. Member ban is removed

**Required Permissions**: `servers.ban`

## Server Moderation

### Moderation Queue

View and handle moderation items:
- Reported messages
- Flagged content
- Automod actions
- Member reports

### Handling Reports

1. Go to Moderation tab
2. Select report
3. Review content and context
4. Choose action:
   - Dismiss (no action)
   - Warn user
   - Delete content
   - Ban user
5. Add notes if needed
6. Confirm action

**Required Permissions**: `reports.resolve`

## Server Analytics

### Activity Metrics

View server performance:
- Active member count
- Message volume
- Join/leave rates
- Moderation actions

### Export Reports

Generate server activity reports:
1. Open server details
2. Click "Generate Report"
3. Select date range
4. Choose report type
5. Generate and export

**Required Permissions**: `servers.read`

## Troubleshooting

### Server Not Loading
- Check server ID is correct
- Verify server exists
- Check for system errors
- Refresh the page

### Cannot Modify Server
- Verify required permissions
- Check if approval workflow is blocking
- Server may be protected
- Review audit logs

### Member Changes Not Applying
- Refresh member list
- Check for system errors
- Verify action completed
- Member may need to rejoin

## Best Practices

1. **Document Changes**: Always provide reasons for server modifications
2. **Regular Reviews**: Periodically review server settings and members
3. **Communication**: Notify server owners of significant changes
4. **Backup**: Export important server data before deletion
5. **Monitoring**: Regularly monitor server activity and moderation queue