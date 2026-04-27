# User Management

This guide covers user account management operations in the Plexichat admin panel.

## Overview

The user management system allows admins to:
- View and search user accounts
- Edit user profiles and settings
- Manage user account status
- Handle scheduled deletions
- Add internal admin notes
- Manage user tiers and badges

## Viewing Users

### User Search

Navigate to User Management and use the search functionality:
- **By User ID**: Enter exact snowflake ID
- **By Username**: Partial or exact username match
- **By Email**: Email address search
- **By Status**: Filter by account status

### User Profile

Click on any user to view their complete profile including:
- Basic information (username, ID, email)
- Account status and tier
- Registration date and last activity
- Associated servers and roles
- Internal admin notes
- Account deletion status

## User Account Operations

### Editing User Profiles

1. Open user profile
2. Click "Edit Profile"
3. Modify allowed fields:
   - Display name
   - Email address
   - Bio/description
   - Profile settings
4. Save changes
5. Action is logged to audit trail

**Required Permissions**: `users.edit`

### Locking User Accounts

Lock a user account to prevent login:

1. Open user profile
2. Click "Lock Account"
3. Provide reason for lock
4. Confirm action
5. User cannot login while locked

**Required Permissions**: `users.lock`

### Unlocking User Accounts

1. Open locked user profile
2. Click "Unlock Account"
3. Confirm action
4. User can login again

**Required Permissions**: `users.lock`

### Forcing Username Changes

1. Open user profile
2. Click "Force Username Change"
3. Enter new username
4. Provide reason
5. Confirm action
6. User must choose new username on next login

**Required Permissions**: `users.force_username_change`

### Forcing Password Changes

1. Open user profile
2. Click "Force Password Change"
3. Confirm action
4. User must change password on next login

**Required Permissions**: `users.force_password_change`

## Account Deletion

### Scheduled Deletion

Users can request account deletion, which starts a grace period:

1. User requests deletion
2. Account enters "scheduled deletion" state
3. Grace period begins (default 30 days)
4. User can cancel during grace period
5. After grace period, account is permanently deleted

**Admin Actions for Scheduled Deletions**:

#### Cancel Deletion
1. Go to Deletions tab
2. Find user in scheduled deletions list
3. Click "Cancel"
4. Confirm action
5. Account is restored to active status

**Required Permissions**: `users.edit`

#### Extend Grace Period
1. Go to Deletions tab
2. Find user in scheduled deletions list
3. Click "Delay"
4. Select new deletion date
5. Confirm action
6. Grace period is extended

**Required Permissions**: `users.edit`

#### Force Purge (Immediate Deletion)
**WARNING: DANGEROUS OPERATION** - This cannot be undone!

1. Go to Deletions tab
2. Find user in scheduled deletions list
3. Click "Purge"
4. Confirm warning
5. Type "DELETE" to confirm
6. Account and all data are immediately deleted

**Required Permissions**: `users.force_purge` (typically requires approval)

### Direct Account Deletion

Delete a user account immediately:

1. Open user profile
2. Click "Delete Account"
3. Provide reason
4. Confirm action
5. Account is deleted immediately

**Required Permissions**: `users.delete` (typically requires approval)

## User Tiers

### Viewing User Tiers

User tiers determine account capabilities and limits:
- **Free**: Basic features with limits
- **Premium**: Enhanced features
- **Enterprise**: Full feature access

### Modifying User Tiers

1. Open user profile
2. Click "Change Tier"
3. Select new tier
4. Provide reason
5. Confirm action
6. User's capabilities are updated

**Required Permissions**: `users.tier`

## User Badges

### Viewing Badges

Badges are displayed on user profiles and indicate:
- Special status (moderator, contributor, etc.)
- Achievements
- Event participation
- Custom badges

### Assigning Badges

1. Open user profile
2. Click "Add Badge"
3. Select badge from available list
4. Add optional notes
5. Confirm action

**Required Permissions**: `users.badges`

### Removing Badges

1. Open user profile
2. Find badge in user's badges list
3. Click "Remove"
4. Confirm action

**Required Permissions**: `users.badges`

## Internal Admin Notes

### Adding Notes

Add private notes visible only to admins:

1. Open user profile
2. Click "Add Note"
3. Enter note content
4. Select note category (optional)
5. Save note
6. Note is visible to all admins with `users.notes` permission

**Required Permissions**: `users.notes`

### Viewing Notes

Notes are displayed in the user profile with:
- Note content
- Author and timestamp
- Note category
- Edit history

### Editing Notes

1. Find note in user's notes section
2. Click "Edit"
3. Modify content
4. Save changes
5. Previous version is preserved

**Required Permissions**: `users.notes`

## Bulk Operations

### Bulk User Actions

Perform actions on multiple users:

1. Go to User Management
2. Select users using checkboxes
3. Choose action from bulk actions menu:
   - Lock accounts
   - Unlock accounts
   - Change tier
   - Add badges
   - Send notifications
4. Configure action parameters
5. Confirm action

**Required Permissions**: Depends on action selected

### Bulk Import

Import users from CSV file:

1. Go to User Management
2. Click "Import Users"
3. Upload CSV file with required columns
4. Map CSV columns to user fields
5. Review import preview
6. Confirm import
7. Users are created with specified settings

**Required Permissions**: `users.create`

## User Activity Monitoring

### Viewing User Activity

User profiles show:
- Last login time
- Last activity timestamp
- Recent messages
- Server memberships
- Account changes history

### Activity Reports

Generate activity reports:
1. Go to User Management
2. Click "Activity Reports"
3. Select date range
4. Choose report type:
   - Login activity
   - Message activity
   - Server activity
   - Account changes
5. Generate report
6. Export if needed

**Required Permissions**: `users.read`

## Troubleshooting

### User Cannot Login
- Check if account is locked
- Verify account is not in deletion process
- Check if user needs password change
- Review audit logs for login attempts

### User Not Found in Search
- Verify search criteria
- Check if user ID is correct
- User may have been deleted
- Check for typos in username

### Cannot Modify User
- Verify you have required permissions
- Check if approval workflow is blocking action
- User may be protected (system account)
- Check audit logs for specific error

### Changes Not Applying
- Refresh user profile
- Check for system errors in logs
- Verify action completed successfully
- User may need to re-login for some changes

## Best Practices

1. **Document Actions**: Always provide reasons for account changes
2. **Use Notes**: Add internal notes for important user information
3. **Regular Reviews**: Periodically review locked and scheduled deletion accounts
4. **Communication**: Notify users of significant account changes
5. **Backup**: Export user data before deletion if needed