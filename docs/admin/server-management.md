# Manage servers

This guide describes how to manage servers in the Plexichat admin panel, including viewing servers, editing settings, managing members, and handling moderation.

## View servers

### List servers

Go to **Server management** to view all servers. The list shows:

- Server name and ID
- Member count
- Owner information
- Creation date
- Activity status

Required permission: `servers.read`.

### View server details

Select a server to view:

- Basic server information
- Member list and roles
- Server settings
- Activity metrics
- Moderation history

Required permission: `servers.read`.

## Edit server settings

1.  Open the server details page.
2.  Click **Edit settings**.
3.  Modify the server configuration.
4.  Save your changes.

Changes take effect immediately.

Required permission: `servers.edit`.

## Delete a server

> **Warning:** This operation deletes the server and all its data and cannot be undone.

1.  Open the server details page.
2.  Click **Delete server**.
3.  Enter a reason.
4.  Confirm the action.

Required permission: `servers.delete`. This action often requires approval — see the [RBAC guide](rbac.md) for approval workflows.

## Manage members

### View members

The member list shows:

- Username and user ID
- Roles
- Join date
- Activity status

Required permission: `servers.read` to view, `servers.edit` to change roles.

### Change member roles

1.  Open the server details page.
2.  Go to the **Members** tab.
3.  Select the member.
4.  Click **Edit roles**.
5.  Add or remove roles.
6.  Save your changes.

Required permission: `servers.edit`.

### Ban a member

1.  Open the server details page.
2.  Go to the **Members** tab.
3.  Select the member.
4.  Click **Ban**.
5.  Enter a reason and duration.
6.  Confirm the action.

Required permission: `admin.moderation.bans`.

There is no `servers.ban` scope. Server member bans are managed under platform-wide moderation. The API is `POST /api/v1/admin/blocked-users` and requires `admin.moderation.bans` (`src/api/routes/admin/moderation.py` and `src/core/admin/permissions.py:42`). The same scope is required to unban.

### Unban a member

1.  Open the server details page.
2.  Go to the **Members** tab.
3.  Select the banned member.
4.  Click **Unban**.
5.  Confirm the action.

Required permission: `admin.moderation.bans` (`DELETE /api/v1/admin/blocked-users/{user_id}`).

## Moderate servers

### Use the moderation queue

View and handle moderation items:

- Reported messages
- Flagged content
- AutoMod actions
- Member reports

### Handle reports

1.  Go to the **Moderation** tab.
2.  Select the report.
3.  Review the content and context.
4.  Choose an action:
    - Dismiss (no action)
    - Warn the user
    - Delete the content
    - Ban the user
5.  Add notes if needed.
6.  Confirm the action.

Required permission: `reports.action`.

There is no `reports.resolve` scope. Use `reports.action` to review hash reports (`POST /api/v1/admin/hash-reports/{id}/review`), message reports (`POST /api/v1/admin/message-reports/{id}/review`), and user reports (`POST /api/v1/admin/user-reports/{id}/review`) — see `src/api/routes/admin/moderation.py` and `src/core/admin/permissions.py:42`.

### Manage AutoMod

- View rules: `GET /api/v1/admin/automod/rules?server_id={id}` (any authenticated admin).
- Create a rule: `POST /api/v1/admin/automod/rules` requires `admin.moderation.rules`.
- Update or delete a rule: `PATCH` or `DELETE /api/v1/admin/automod/rules/{id}` requires `admin.moderation.rules`.
- View or update global config: `GET` or `PUT /api/v1/admin/automod/config` (update requires `admin.moderation.config`).

### Manage blocked hashes

- List blocked hashes: `GET /api/v1/admin/blocked-hashes`.
- Block a hash: `POST /api/v1/admin/blocked-hashes` requires `admin.moderation.hashes`.
- Unblock a hash: `DELETE /api/v1/admin/blocked-hashes/{hash}` requires `admin.moderation.hashes`.

## View analytics

### Check activity metrics

View server performance:

- Active member count
- Message volume
- Join and leave rates
- Moderation actions

Required permission: `servers.read` or `metrics.read` for system-wide metrics.

### Export reports

1.  Open the server details page.
2.  Click **Generate report**.
3.  Select a date range.
4.  Choose a report type.
5.  Generate and export the report.

Required permission: `servers.read`.

To export audit evidence for server actions, use `GET /api/v1/admin/audit/logs?target_type=server` or `GET /api/v1/admin/audit/logs/export?format=csv` with `admin.audit.read`. The path is `/audit/logs`, not `/audit-log` (`src/api/routes/admin/audit.py:34`).

## Troubleshoot

### A server does not load

- Verify that the server ID is correct and the server exists.
- Check for system errors and refresh the page.

### You cannot modify a server

- Verify that you have the required permission (`servers.edit` or `servers.delete`).
- Check whether an approval workflow is blocking the action.
- Review the audit log at `GET /api/v1/admin/audit/logs` for details.

### Member changes do not apply

- Refresh the member list.
- Check for system errors and verify that the action completed.
- The member may need to rejoin.

## Best practices

- **Document changes.** Always provide a reason for server modifications. The audit log records these reasons.
- **Review regularly.** Periodically review server settings, members, and moderation queues.
- **Communicate.** Notify server owners of significant changes.
- **Back up before deletion.** Export important server data before you delete a server.
- **Monitor activity.** Regularly check server activity and the moderation queue.
