# Approval Workflows

Plexichat's approval workflow system provides multi-admin approval for sensitive operations, ensuring critical actions require consensus before execution.

## Overview

Approval workflows require multiple admins to approve sensitive actions before they can be executed, providing:
- Additional security layer for critical operations
- Audit trail of approval decisions
- Configurable approval requirements
- Timeout handling for stale requests
- Comment-based discussion on pending approvals

## Configuration

Approval workflows are configured in the `admin_ui.approval_workflows` section:

```yaml
admin_ui:
  approval_workflows:
    enabled: true
    single_admin_bypass: true
    require_approval_for:
      - users.force_purge
      - users.delete
      - servers.delete
    approval_required_admins: 2
    approval_timeout_hours: 48
```

### Configuration Options

| Option | Description | Default |
|--------|-------------|---------|
| `enabled` | Enable or disable approval workflows | `true` |
| `single_admin_bypass` | Allow single admin to approve if they're the only admin | `true` |
| `require_approval_for` | List of actions requiring approval | `[users.force_purge, users.delete, servers.delete]` |
| `approval_required_admins` | Number of admins required to approve | `2` |
| `approval_timeout_hours` | Hours before approval request expires | `48` |

## Actions Requiring Approval

By default, the following actions require approval:

- `users.force_purge` - Immediate user account purge
- `users.delete` - User account deletion
- `servers.delete` - Server deletion

You can add more actions to the `require_approval_for` list in the configuration.

## Approval Process

### 1. Request Creation
When an admin attempts a sensitive action:
1. System checks if action requires approval
2. If yes, creates an approval request in `admin_approvals` table
3. Notifies other admins of pending approval
4. Action is not executed until approved
5. If `single_admin_bypass` is enabled and only one admin exists, approval is bypassed

### 2. Discussion & Voting
Other admins can:
- **Approve** - Add their approval to the request
- **Reject** - Reject the request with a reason
- **Comment** - Add comments to facilitate discussion
- **Cancel** - Cancel their own request or (as super admin) any pending request

### 3. Execution
Once required approvals are reached:
- System status changes to `approved`
- The original action can then be executed
- Logs the completion with approval details

## Database Schema

### admin_approvals
```sql
CREATE TABLE admin_approvals (
    id INTEGER PRIMARY KEY,
    requested_by INTEGER NOT NULL,
    action_type VARCHAR(100) NOT NULL,
    target_type VARCHAR(50),
    target_id INTEGER,
    action_details TEXT,
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    required_approvals INTEGER NOT NULL DEFAULT 2,
    current_approvals INTEGER NOT NULL DEFAULT 0,
    approved_by TEXT,
    rejected_by INTEGER,
    rejection_reason TEXT,
    expires_at INTEGER,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
)
```

### admin_approval_comments
```sql
CREATE TABLE admin_approval_comments (
    id INTEGER PRIMARY KEY,
    approval_id INTEGER NOT NULL,
    admin_id INTEGER NOT NULL,
    comment TEXT NOT NULL,
    created_at INTEGER NOT NULL
)
```

## Approval States

| State | Description |
|-------|-------------|
| `pending` | Awaiting approvals |
| `approved` | Sufficient approvals received |
| `rejected` | Request rejected by an admin |
| `expired` | Request timed out |
| `cancelled` | Request cancelled by requester or super admin |

## API Endpoints

### Create Approval Request
```bash
POST /api/v1/admin/approvals/request
{
  "action_type": "users.force_purge",
  "target_type": "user",
  "target_id": 12345,
  "action_details": "Spam account - multiple reports"
}
```

### List Approvals (with optional filters)
```bash
# All pending approvals
GET /api/v1/admin/approvals?status=pending

# Approvals for a specific action
GET /api/v1/admin/approvals?action_type=users.force_purge

# Combined filtering
GET /api/v1/admin/approvals?status=pending&action_type=users.force_purge
```

### Get Approval Details
```bash
GET /api/v1/admin/approvals/{approval_id}
```

### Approve Request
```bash
POST /api/v1/admin/approvals/{approval_id}/approve
```

### Reject Request
```bash
POST /api/v1/admin/approvals/{approval_id}/reject
{
  "decision": "reject",
  "reason": "Insufficient evidence for purge"
}
```

### Cancel Request
```bash
DELETE /api/v1/admin/approvals/{approval_id}
```

Only the original requester or a super admin can cancel a pending request.

### Add Comment to Approval
```bash
POST /api/v1/admin/approvals/{approval_id}/comments
{
  "comment": "I've reviewed the evidence and it looks legitimate"
}
```

### Get Approval Comments
```bash
GET /api/v1/admin/approvals/{approval_id}/comments
```

## Single Admin Bypass

When `single_admin_bypass` is enabled and there's only one admin in the system:
- Approval requirements are automatically bypassed
- Actions execute immediately
- Still logged for audit purposes
- Warning logged indicating bypass occurred

This ensures system operability while maintaining security for multi-admin deployments.

## Security Considerations

1. **Approval Permissions**: Only admins with `admin.approvals` permission can view/manage approvals
2. **Self-Approval Prevention**: Admins cannot approve their own requests
3. **Duplicate Approval Prevention**: Each admin can only approve once per request
4. **Timeout Handling**: Expired requests are auto-rejected on check
5. **Audit Trail**: All approval actions are logged to the admin audit log
6. **Hierarchy Enforcement**: Super admin privileges are required to cancel other admin's requests

## Best Practices

1. **Clear Action Descriptions**: Provide detailed context when requesting approval
2. **Timely Review**: Respond to approval requests promptly
3. **Use Comments**: Document approval/rejection reasoning with comments
4. **Regular Cleanup**: Review and clean up expired/approved requests
5. **Appropriate Thresholds**: Set approval requirements based on risk level
6. **Review Expired Requests**: Consider re-submitting if a request times out

## Troubleshooting

### Approval Request Not Created
- Check if approval workflows are enabled in config
- Verify the action is in the `require_approval_for` list
- Ensure admin has `admin.approvals` permission

### Approvals Not Counting
- Verify the approving admin hasn't already approved this request
- Ensure request is still in `pending` status
- Check the admin is not the original requester (self-approval blocked)

### Action Not Executing After Approval
- Check that sufficient approvals have been received
- Verify request status changed to `approved`
- The original action must be separately triggered after approval

### Requests Timing Out
- Review the `approval_timeout_hours` setting (default: 48 hours)
- Ensure admins are notified of pending requests
- Consider increasing timeout for complex reviews
