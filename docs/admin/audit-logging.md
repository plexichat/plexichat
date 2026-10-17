# Audit logging

Plexichat records every admin action in an audit trail that you can query, filter, and export. Use this guide to configure logging, understand the schema, and retrieve logs for compliance.

## Before you begin

Verify that you have:

* An admin account with the `admin.audit.read` permission. See [RBAC](rbac.md).
* Access to `admin_ui.audit` settings in your configuration file or admin panel.

Audit logging is implemented in `src/core/admin/logging.py` and `src/core/admin/permissions.py:459`. The query and export API is in `src/api/routes/admin/audit.py`.

## Configure audit logging

Configure audit logging under `admin_ui.audit` in `config.yaml`:

```yaml
admin_ui:
  audit:
    log_to_file: true
    log_to_database: true
    sensitive_actions_always_db: true
    retention_days: 365
```

### Configuration options

| Option | Type | Default | Description |
|---|---|---|---|
| `log_to_file` | boolean | `true` | Write audit entries to the file logger (`admin` logger). |
| `log_to_database` | boolean | `true` | Write audit entries to the `admin_audit_log` table. |
| `sensitive_actions_always_db` | boolean | `true` | Always write sensitive actions to the database, even if `log_to_database` is `false`. |
| `retention_days` | integer | `365` | How long to retain database rows before cleanup. |

Sensitive actions that always go to the database include `users.force_purge`, `users.delete`, `servers.delete`, and `config.modify`. See `src/core/admin/permissions.py:489`.

Note: The file logger uses the standard Python `logging` package. Configure rotation with `logging.max_bytes`, `logging.backup_count`, and `logging.rotate` at the top level of your config.

## Understand the database schema

The `admin_audit_log` table stores one row per admin action:

```sql
CREATE TABLE admin_audit_log (
    id INTEGER PRIMARY KEY,
    admin_id INTEGER NOT NULL,
    action VARCHAR(100) NOT NULL,
    target_type VARCHAR(50),
    target_id INTEGER,
    target_user_id INTEGER,
    details TEXT,
    ip_address VARCHAR(45),
    user_agent TEXT,
    status VARCHAR(20) NOT NULL DEFAULT 'success',
    created_at INTEGER NOT NULL
);
```

* `created_at` is a Unix timestamp in **milliseconds**.
* `status` is `success`, `failed`, or `pending_approval`.
* `target_type` describes what was acted on, for example `user`, `server`, or `approval`.
* `target_user_id` is populated when the entry relates to a user, either from `metadata.target_user_id` or from `target_type = 'user'`.

The write path is in `src/core/admin/logging.py:124` and handles both the legacy schema without `target_user_id` and the current schema with it.

## Review logged actions

The system logs the following families of actions:

### Authentication

`login`, `login_failed`, `logout`, `otp_setup`, `otp_verify`, `password_change`

### User management

`user.create`, `user.edit`, `user.delete`, `user.force_purge`, `user.lock`, `user.unlock`, `user.tier_change`, `user.badge_add`, `user.badge_remove`, `users.force_username_change`

### Server management

`server.create`, `server.edit`, `server.delete`, `server.ban`

### Admin and RBAC

`admin.create`, `admin.edit`, `admin.delete`, `role.assign`, `role.revoke`, `force_password_change`, `admin.approvals`

### System operations

`config.modify`, `migration.run`, `migration.rollback`, `system.restart`, `approval_request:*`, `approval_approve`, `approval_reject`

## Query audit logs

### Using the admin API

You need `admin.audit.read` to call these endpoints. They are defined in `src/api/routes/admin/audit.py:34`.

List logs with filters:

```http
GET /api/v1/admin/audit/logs?admin_id=123&action_family=user&status=success&page=1&per_page=50
```

Query parameters:

| Parameter | Type | Description |
|---|---|---|
| `action_family` | string | Prefix filter on `action`, for example `user`, `login`, or `approval`. |
| `status` | string | Filter by `success` or `failed`. |
| `admin_id` | integer | Filter by the acting admin. |
| `target_type` | string | Filter by `user`, `server`, `admin`, or `approval`. |
| `date_from` | integer | Start timestamp in milliseconds. |
| `date_to` | integer | End timestamp in milliseconds. |
| `page` | integer | Page number, starting at `1`. Default `1`. |
| `per_page` | integer | Items per page, `1` to `200`. Default `50`. |

Example response:

```json
{
  "logs": [
    {
      "id": 987654321,
      "admin_id": 123,
      "action": "user.delete",
      "target_type": "user",
      "target_id": 456,
      "details": "Reason: spam account",
      "ip_address": "203.0.113.10",
      "status": "success",
      "created_at": 1704067200000,
      "timestamp": "2024-01-01T00:00:00"
    }
  ],
  "total": 1,
  "page": 1,
  "per_page": 50,
  "pages": 1
}
```

Get today's summary statistics:

```http
GET /api/v1/admin/audit/logs/stats
```

Export logs as CSV or JSON:

```http
GET /api/v1/admin/audit/logs/export?format=csv&action_family=user&date_from=1704067200000
GET /api/v1/admin/audit/logs/export?format=json&status=failed
```

The export endpoint is defined in `src/api/routes/admin/audit.py:209` and returns `text/csv` or `application/json` with a `Content-Disposition: attachment` header.

### Using the admin panel

1. Go to **Audit Log**.
2. Filter by date range, admin, action family, target type, or status.
3. Open an entry to view details, IP address, and user agent.
4. Click **Export** to download CSV or JSON for compliance reporting.

### Using SQL

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
    datetime(created_at / 1000, 'unixepoch') AS created_at
FROM admin_audit_log
WHERE created_at > ?
ORDER BY created_at DESC
LIMIT 100;
```

Replace `?` with the start timestamp in milliseconds.

## Manage retention

### File logs

File logs are managed by the system logger:

* Rotate based on `logging.max_bytes` and `logging.backup_count`.
* Compress old logs when `logging.zip_logs` is `true`.

### Database logs

Delete rows older than your retention period:

```sql
DELETE FROM admin_audit_log
WHERE created_at < ?;
```

Set `?` to the cutoff timestamp: `now_ms - (retention_days * 24 * 60 * 60 * 1000)`. The configured default is 365 days.

Caution: Deleting audit rows is irreversible. Export data before you run cleanup jobs if you need an archive.

## Meet compliance requirements

### Immutable logs

* Admins cannot modify rows in `admin_audit_log` through the API.
* The table is protected by the `admin.audit.read` permission.
* File logs are append-only. Use write-once storage for critical deployments.

### Complete trail

Every sensitive action records the admin ID, action, target, timestamp with millisecond precision, IP address, user agent, and success or failure status. See `src/core/admin/logging.py:18` for the `AdminLogEntry` structure.

### Exports

Use the export endpoints for analysis and reporting:

* **CSV** — Open in spreadsheets or SIEM tools.
* **JSON** — Integrate with external compliance systems.

## Follow security best practices

* Restrict `admin.audit.read` to a small set of admins. See [RBAC](rbac.md).
* Back up audit logs regularly and store backups securely.
* Alert on suspicious patterns, such as repeated `login_failed` or `users.force_purge` actions.
* Keep `sensitive_actions_always_db` enabled so destructive actions are always recorded.

## Troubleshoot audit logging

### Logs do not appear

* Check that `admin_ui.audit.log_to_file` or `log_to_database` is `true`.
* Verify the database is reachable. The API returns `503` when the database is unavailable.
* Check file system permissions for the log directory, which defaults to `~/.plexichat/logs`.

### Sensitive actions are missing

* Confirm `sensitive_actions_always_db` is `true`.
* Verify the action is in the sensitive list in `src/core/admin/permissions.py:498`.

### Database grows too large

* Reduce `retention_days` or run more frequent cleanup jobs.
* Export and archive old rows to separate storage before deleting.
* Add an index on `created_at` or `action` if query latency increases.

### Performance is affected

* Monitor slow queries with `database.monitoring.slow_query_threshold_ms`.
* Consider asynchronous logging for high-traffic deployments.
* Limit `per_page` to `50` or fewer when you query frequently.

## Related documentation

* [RBAC](rbac.md) — Permission required to read audit logs (`admin.audit.read`).
* [Approval workflows](approval-workflows.md) — Approval actions appear in the audit trail.
* [Configuration overview](../configuration.md) — Where and how to set `admin_ui.audit`.
