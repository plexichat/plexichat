# Approval Workflows

Plexichat's approval workflow system provides multi-admin approval for sensitive operations, ensuring critical actions require consensus before execution.

## Overview

Approval workflows require multiple admins to approve sensitive actions before they can be executed, providing:
- Additional security layer for critical operations
- Audit trail of approval decisions
- Configurable approval requirements
- Timeout handling for stale requests

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
    auto_approve_after_hours: 72
```

### Configuration Options

- `enabled`: Enable or disable approval workflows
- `single_admin_bypass`: Allow single admin to approve if they're the only admin
- `require_approval_for`: List of actions requiring approval
- `approval_required_admins`: Number of admins required to approve
- `approval_timeout_hours`: Hours before approval request expires
- `auto_approve_after_hours`: Hours after which requests are auto-approved (if configured)

## Actions Requiring Approval

By default, the following actions require approval:

- `users.force_purge` - Immediate user account purge
- `users.delete` - User account deletion
- `servers.delete` - Server deletion

You can add more actions to the `require_approval_for` list as needed.

## Approval Process

### 1. Request Creation
When an admin attempts a sensitive action:
1. System checks if action requires approval
2. If yes, creates an approval request in `admin_approvals` table
3. Notifies other admins of pending approval
4. Action is not executed until approved

### 2. Approval Voting
Other admins can:
- **Approve** - Add their approval to the request
- **Reject** - Reject the request with reason
- **Comment** - Add comments for discussion

### 3. Execution
Once required approvals are reached:
- System executes the original action
- Logs the completion with approval details
- Notifies all involved admins

## Database Schema

The `admin_approvals` table stores approval requests:

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

## Approval States

- `pending` - Awaiting approvals
- `approved` - Sufficient approvals received
- `rejected` - Request rejected by an admin
- `expired` - Request timed out
- `cancelled` - Request cancelled by requester

## Managing Approvals

### Via Admin Panel
1. Navigate to Approvals section
2. View pending approval requests
3. Review action details and context
4. Approve or reject with optional comments
5. Track approval status

### Via API

#### Create Approval Request
```bash
POST /api/v1/admin/approvals/request
{
  "action_type": "users.force_purge",
  "target_type": "user",
  "target_id": 12345,
  "action_details": "Spam account - multiple reports"
}
```

#### Approve Request
```bash
POST /api/v1/admin/approvals/{approval_id}/approve
```

#### Reject Request
```bash
POST /api/v1/admin/approvals/{approval_id}/reject
{
  "reason": "Insufficient evidence for purge"
}
```

#### View Pending Approvals
```bash
GET /api/v1/admin/approvals?status=pending
```

## Best Practices

1. **Clear Action Descriptions**: Provide detailed context when requesting approval
2. **Timely Review**: Respond to approval requests promptly
3. **Document Decisions**: Use comments to explain approval/rejection reasoning
4. **Regular Cleanup**: Review and clean up expired/approved requests
5. **Appropriate Thresholds**: Set approval requirements based on risk level

## Security Considerations

1. **Approval Permissions**: Only admins with appropriate permissions can approve
2. **Request Validation**: Validate all approval requests before execution
3. **Audit Trail**: Maintain complete audit trail of approval process
4. **Timeout Handling**: Properly handle expired approval requests
5. **Self-Approval**: Prevent admins from approving their own requests

## Troubleshooting

### Approval Request Not Created
- Check if approval workflows are enabled
- Verify the action is in `require_approval_for` list
- Ensure admin has permission to request approval

### Approvals Not Counting
- Verify admin has permission to approve
- Check that approval hasn't already been given by same admin
- Ensure request is still in `pending` status

### Action Not Executing After Approval
- Check that required approval count is reached
- Verify request status changed to `approved`
- Check for execution errors in logs

### Requests Timing Out
- Review `approval_timeout_hours` setting
- Ensure admins are notified of pending requests
- Consider increasing timeout if needed

## Single Admin Bypass

When `single_admin_bypass` is enabled and there's only one admin in the system:
- Approval requirements are automatically bypassed
- Actions execute immediately
- Still logged for audit purposes
- Warning logged indicating bypass occurred

This ensures system operability while maintaining security for multi-admin deployments.