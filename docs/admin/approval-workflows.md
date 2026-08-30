# Approval workflows

Require multiple admins to approve sensitive operations before they execute. Use approval workflows to add a consensus step for destructive actions, keep an audit trail of decisions, and handle timeouts for stale requests.

## Before you begin

You need:

* An admin account with `admin.approvals` permission. See [RBAC](rbac.md).
* `admin_ui.approval_workflows.enabled` set to `true` in your configuration.

All enforcement logic is in `src/core/admin/permissions.py:213` and `src/core/admin/logging.py:300`.

## Configure approval workflows

Configure workflows under `admin_ui.approval_workflows` in `config.yaml`:

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

### Configuration options

| Option | Type | Default | Description |
|---|---|---|---|
| `enabled` | boolean | `true` | Enable or disable approval workflows. |
| `single_admin_bypass` | boolean | `true` | Bypass approval when only one admin exists. |
| `require_approval_for` | array of strings | `["users.force_purge", "users.delete", "servers.delete"]` | Actions that require approval. |
| `approval_required_admins` | integer | `2` | How many distinct admins must approve. |
| `approval_timeout_hours` | integer | `48` | Hours before a pending request expires. |
| `auto_approve_after_hours` | integer | `72` | Hours after which the system can auto-approve, if that feature is enabled in your deployment. |

You can add any action from the approval list in `src/core/admin/permissions.py:122` to `require_approval_for`, including `config.security`, `admin.roles`, `migrations.run`, `search.reindex`, and `artifacts.force_delete`.

## Understand actions that require approval

By default, the following actions require approval:

* `users.force_purge` — Immediately purge a user account.
* `users.delete` — Delete a user account.
* `servers.delete` — Delete a server.

To require approval for additional sensitive operations, add them to `require_approval_for`. For example, to require approval before modifying security configuration:

```yaml
admin_ui:
  approval_workflows:
    require_approval_for:
      - users.force_purge
      - users.delete
      - servers.delete
      - config.security
      - admin.roles
```

## Review the approval lifecycle

### 1. Request creation

When an admin attempts a sensitive action:

1. The system checks whether `enabled` is `true` and whether the action is in `require_approval_for`.
2. If `single_admin_bypass` is `true` and only one row exists in `admin_users`, the action bypasses approval and executes immediately. See `src/core/admin/permissions.py:238`.
3. Otherwise, the system inserts a row into `admin_approvals` with `status = 'pending'` and notifies other admins.
4. Super-admin holders of `*` bypass approval checks. See `src/core/admin/permissions.py:250`.

The original action does not execute until the request reaches `approved`.

### 2. Discussion and voting

Admins with `admin.approvals` can:

* **Approve** the request.
* **Reject** the request with a reason.
* **Comment** on the request to discuss evidence.
* **Cancel** their own request, or any pending request if they are a super admin.

The system enforces these rules:

* An admin cannot approve their own request.
* Each admin can approve a request only once.
* A rejected or expired request cannot be approved.

### 3. Execution

After `current_approvals` reaches `required_approvals`:

* The row status changes to `approved`.
* The original action becomes eligible to execute.
* The decision is written to `admin_audit_log`.

## Review the database schema

### admin_approvals table

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
);
```

* `expires_at` and `created_at` are Unix timestamps in milliseconds.
* `approved_by` stores a JSON array of admin IDs that approved.
* `status` is `pending`, `approved`, `rejected`, `expired`, or `cancelled`.

### admin_approval_comments table

```sql
CREATE TABLE admin_approval_comments (
    id INTEGER PRIMARY KEY,
    approval_id INTEGER NOT NULL,
    admin_id INTEGER NOT NULL,
    comment TEXT NOT NULL,
    created_at INTEGER NOT NULL
);
```

## Use the approval API

All approval endpoints require the `admin.approvals` permission unless noted. Host restrictions from `admin_ui.host_restriction` also apply.

### Create an approval request

```http
POST /api/v1/admin/approvals/request
Content-Type: application/json

{
  "action_type": "users.force_purge",
  "target_type": "user",
  "target_id": 12345,
  "action_details": "Spam account - multiple reports"
}
```

The system creates this request automatically when you call a sensitive endpoint that requires approval.

### List approvals

```http
GET /api/v1/admin/approvals?status=pending
GET /api/v1/admin/approvals?action_type=users.force_purge
GET /api/v1/admin/approvals?status=pending&action_type=users.force_purge
```

### Get approval details

```http
GET /api/v1/admin/approvals/{approval_id}
```

### Approve a request

```http
POST /api/v1/admin/approvals/{approval_id}/approve
```

### Reject a request

```http
POST /api/v1/admin/approvals/{approval_id}/reject
Content-Type: application/json

{
  "decision": "reject",
  "reason": "Insufficient evidence for purge"
}
```

### Cancel a request

```http
DELETE /api/v1/admin/approvals/{approval_id}
```

You can cancel a pending request if you are the original requester or a super admin (position 100).

### Comment on a request

```http
POST /api/v1/admin/approvals/{approval_id}/comments
Content-Type: application/json

{
  "comment": "I reviewed the evidence and it looks legitimate."
}
```

Get comments:

```http
GET /api/v1/admin/approvals/{approval_id}/comments
```

## Handle single-admin deployments

When `single_admin_bypass` is `true` and only one admin exists in `admin_users`:

* Approval is bypassed automatically.
* The action executes immediately.
* The bypass is logged for audit purposes with a warning.

This keeps single-admin deployments operable. For multi-admin deployments, keep `single_admin_bypass` enabled only if you want the first admin to operate without waiting during initial setup. Disable it once a second admin exists if you require consensus for every sensitive action.

## Understand security enforcement

| Rule | How it is enforced |
|---|---|
| View and manage approvals | Requires `admin.approvals`. See `src/core/admin/permissions.py:87` in `PERMISSION_SCOPES`. |
| No self-approval | The requester cannot count toward `current_approvals`. |
| One approval per admin | `approved_by` tracks distinct admin IDs. |
| Timeout handling | Requests with `expires_at` in the past transition to `expired` on the next check. |
| Hierarchy for cancellation | Only the requester or an admin at position 100 can cancel another admin's request. |
| Audit trail | Every request, approval, rejection, cancellation, and expiration writes to `admin_audit_log`. |

## Follow best practices

* Provide clear `action_details` so reviewers can decide quickly.
* Respond to pending requests promptly. The default timeout is 48 hours.
* Use comments to document the reason for each approval or rejection.
* Review expired and approved rows regularly and clean them up.
* Set `approval_required_admins` and `approval_timeout_hours` based on the risk of the action.

## Troubleshoot approval workflows

### The approval request is not created

* Check that `admin_ui.approval_workflows.enabled` is `true`.
* Verify the action is in `require_approval_for`.
* Confirm the acting admin has the underlying permission for the action (for example, `users.delete`), and if the caller is checking `admin.approvals` explicitly, verify that as well.
* Check `single_admin_bypass`: with one admin, requests are intentionally not created.

### Approvals are not counting

* Confirm the approving admin is not the original requester.
* Verify the request is still `pending`.
* Check that the same admin has not already approved that `approval_id`.

### The action does not execute after approval

* Verify `current_approvals` equals `required_approvals` and `status` is `approved`.
* Call the original action endpoint again after approval. Approval unblocks the action; it does not execute it automatically.

### Requests expire too quickly

* Increase `approval_timeout_hours`.
* Check that admins are notified. Configure `admin_ui.notifications.email_on_approval_required` or `admin_ui.notifications.webhook_url`.
* Review `auto_approve_after_hours` if your deployment uses auto-approval.

## Related documentation

* [RBAC](rbac.md) — How roles and hierarchy control who can approve.
* [Audit logging](audit-logging.md) — How approval decisions appear in the audit trail.
* [Configuration overview](../configuration.md) — Where to set `admin_ui.approval_workflows`.
