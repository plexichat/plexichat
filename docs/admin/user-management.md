# Manage users in the admin panel

This guide describes how to manage user accounts in the Plexichat admin panel.

## Overview

The admin panel lets operators with the appropriate role manage the user lifecycle:

- Search and view user accounts.
- Edit profiles and account settings.
- Lock and unlock accounts.
- Force username changes.
- Handle scheduled deletions, grace periods, and immediate purges.
- Manage tiers and badges.
- Add internal admin notes.

All changes are written to the audit log. Some destructive actions require approval.
See `admin_ui.approval_workflows` in `src/config_defaults.py`.

## Before you begin

You need an admin session authenticated at `<admin-base>` (default `/api/v1/admin`) and a role that contains the permission listed for each action. See [Admin permissions](../admin/rbac.md) and `src/core/admin/permissions.py:42` for the full `PERMISSION_SCOPES` list.

Common scopes:

| Scope | Purpose |
|---|---|
| `users.read` | View users and activity reports |
| `users.edit` | Edit profiles, cancel or delay deletions |
| `users.lock` | Lock and unlock accounts |
| `users.force_username_change` | Force username changes and credential resets |
| `users.tier` | Change account tiers |
| `users.badges` | Add or remove badges |
| `users.notes` | Add, view, and edit internal notes |
| `users.delete` | Delete accounts immediately |
| `users.force_purge` | Immediately purge scheduled deletions |

Caution: Do not document `users.force_password_change`. This scope does not exist. Use `users.force_username_change` for forced account and credential changes. The role API rejects wildcard scopes such as `*` or `users.*` with `400` (`src/api/routes/admin/roles.py:201`).

## Search and view users

### Search for a user

1. Go to **User Management**.
2. Use the search box to filter by:
   - User ID (exact Snowflake ID)
   - Username (partial or exact match)
   - Email address
   - Account status

The search endpoint is `GET <admin-base>/users/search`.

### View a user profile

Select a user to view:

- Basic information (username, ID, email)
- Account status and tier
- Registration date and last activity
- Server memberships and roles
- Internal admin notes
- Scheduled deletion status, if any

The profile endpoint is `GET <admin-base>/users/{user_id}`.

## Edit user profiles

1. Open the user profile.
2. Click **Edit Profile**.
3. Update allowed fields such as display name, email, bio, and profile settings.
4. Click **Save**.

Required permission: `users.edit`.

The API endpoint is `PATCH` or `PUT` on the user resource as exposed by the admin users router (`src/api/routes/admin/users/__init__.py`).

## Lock and unlock accounts

### Lock an account

Locking prevents login without deleting data.

1. Open the user profile.
2. Click **Lock Account**.
3. Enter a reason for the lock.
4. Confirm the action.

Required permission: `users.lock`. The action is `POST <admin-base>/security/lock-user`.

### Unlock an account

1. Open the locked user profile.
2. Click **Unlock Account**.
3. Confirm the action.

Required permission: `users.lock`. The action is `POST <admin-base>/security/unlock-user`.

## Force a username change

1. Open the user profile.
2. Click **Force Username Change**.
3. Enter the new username and a reason.
4. Confirm the action.

The user is prompted to choose a new username on next login.

Required permission: `users.force_username_change`.

Endpoint: `POST <admin-base>/users/{user_id}/force-username-change`.

Note: There is no `users.force_password_change` scope. The admin password change for managed admins is `POST <admin-base>/auth/force-password-change/{target_admin_id}`.

## Handle scheduled deletions

When a user requests deletion, the account enters a scheduled deletion state with a grace period (default 30 days).

### Cancel a scheduled deletion

1. Go to the **Deletions** tab.
2. Find the user in the scheduled deletions list (`GET <admin-base>/users/scheduled-deletions`).
3. Click **Cancel**.
4. Confirm the action.

The account returns to active status.

Required permission: `users.edit`.

Endpoint: `POST <admin-base>/users/{user_id}/cancel-deletion`.

### Extend the grace period

1. Go to the **Deletions** tab.
2. Find the user.
3. Click **Delay**.
4. Select a new deletion date.
5. Confirm the action.

Required permission: `users.edit`.

Endpoint: `POST <admin-base>/users/{user_id}/delay-deletion`.

### Force purge an account

Caution: This action immediately and irreversibly deletes the account and all data.

1. Go to the **Deletions** tab.
2. Find the user.
3. Click **Purge**.
4. Confirm the warning and type `DELETE`.

Required permission: `users.force_purge`. This action typically requires approval from two admins (`admin_ui.approval_workflows.require_approval_for` includes `users.force_purge`).

Endpoint: `POST <admin-base>/users/{user_id}/force-purge`.

### Delete an account directly

1. Open the user profile.
2. Click **Delete Account**.
3. Enter a reason.
4. Confirm the action.

Required permission: `users.delete` (typically requires approval).

## Manage tiers and badges

### View tiers

Tiers determine capabilities and limits. The current tiers are `standard`, `alpha`, `premium`, and `staff` as defined in `src/config_defaults.py:331`. The `available_badges` list is also defined there.

### Change a tier

1. Open the user profile.
2. Click **Change Tier**.
3. Select the new tier.
4. Enter a reason.
5. Confirm the action.

Required permission: `users.tier`.

Endpoint: `PUT <admin-base>/users/{user_id}/tier`.

### View badges

Badges indicate special status, achievements, or event participation and are rendered on the user profile.

### Add a badge

1. Open the user profile.
2. Click **Add Badge**.
3. Select a badge from the allow-list.
4. Add optional notes.
5. Confirm the action.

Required permission: `users.badges`.

Endpoint: `POST <admin-base>/users/{user_id}/badges/{badge}`.

### Remove a badge

1. Find the badge in the user's badge list.
2. Click **Remove**.
3. Confirm the action.

Required permission: `users.badges`.

Endpoint: `DELETE <admin-base>/users/{user_id}/badges/{badge}`.

## Use internal admin notes

Notes are private to admins and never shown to the user.

### Add a note

1. Open the user profile.
2. Click **Add Note**.
3. Enter the content and optionally select a category.
4. Click **Save**.

Required permission: `users.notes`.

Endpoint: `POST <admin-base>/users/{user_id}/notes`.

### View notes

Notes are displayed in the profile with content, author, timestamp, category, and edit history.

Endpoint: `GET <admin-base>/users/{user_id}/notes`.

### Edit a note

1. Find the note.
2. Click **Edit**.
3. Update the content.
4. Save your changes.

The previous version is preserved in the audit trail.

Required permission: `users.notes`.

## Run bulk operations

### Apply bulk actions

1. Go to **User Management**.
2. Select users with the checkboxes.
3. Choose an action from the bulk actions menu: lock, unlock, change tier, add badges, or send notifications.
4. Configure the parameters.
5. Confirm the action.

Required permission: Depends on the selected action.

### Import users from CSV

1. Go to **User Management**.
2. Click **Import Users**.
3. Upload a CSV file with the required columns.
4. Map the CSV columns to user fields.
5. Review the preview.
6. Confirm the import.

Required permission: `users.create` (if enabled in your deployment).

## Monitor activity

### View user activity

Profiles show last login, last activity, recent messages, server memberships, and a history of account changes.

### Generate activity reports

1. Go to **User Management**.
2. Click **Activity Reports**.
3. Select a date range.
4. Choose a report type: login activity, message activity, server activity, or account changes.
5. Generate and optionally export the report.

Required permission: `users.read`.

## Troubleshooting

| Symptom | Check |
|---|---|
| User cannot log in | Verify the account is not locked, not in scheduled deletion, and not pending a forced password or username change. Review audit logs and `POST <admin-base>/security/lock-user` history. |
| User not found in search | Verify the Snowflake ID, check for typos, and confirm the account has not been purged. |
| Cannot modify a user | Confirm you have the required scope, that the approval workflow is not blocking the action, and that the target is not a protected system account. |
| Changes do not appear | Refresh the profile, check server logs, and verify the API response was `200`. Some changes require the user to re-authenticate. |

## Best practices

- Always provide a reason when you change an account. Reasons are stored in the audit log.
- Add an internal note for context that other admins need.
- Regularly review locked accounts and scheduled deletions.
- Notify users of significant changes when appropriate.
- Export user data before deletion if retention policies require it. Use the DSAR endpoints under `GET <admin-base>/data-export`.
