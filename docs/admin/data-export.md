# Data export (DSAR)

Provide users with a machine-readable export of their personal data in response to Data Subject Access Requests (DSARs), per GDPR Article 20 (right to data portability). This guide covers the workflow, configuration, APIs, and security considerations.

## Before you begin

You need:

* An admin account with `data_export.read` to view requests and `data_export.process` to approve, deny, or generate exports. See `src/core/admin/permissions.py:89`.
* Access to `dsar` settings in your configuration file.

The data-export admin API is in `src/api/routes/admin/data_export.py`. The full set of valid permission scopes is in `src/core/admin/permissions.py:42`.

## Understand the workflow

```
User submits request → Admin reviews → Harvester processes → User downloads
       (password)        Approve/Deny      Generate file       Time-limited link
```

### 1. User submits a request

The user sends a POST to `/api/v1/users/@me/data-export` with their password and optional format. The system verifies the password and enforces rate limiting (one request per 24 hours).

### 2. Admin reviews the request

If `dsar.require_admin_review` is `true` (the default), the request stays in `pending` until an admin approves it. Admins review pending requests in the admin panel or by calling `GET /api/v1/admin/data-export`.

### 3. Harvester processes the request

The DSAR harvester is a background worker that picks up approved requests in batches, collects the user's data, and writes an export file. It runs on a configurable interval and at startup if `dsar.harvester.boot_check_enabled` is `true`.

### 4. User downloads the export

When the export is ready, the status becomes `ready` and the user can fetch a download link. Links expire after `dsar.retention_days` (default 7 days).

## Configure data export

Configure DSAR under `dsar` in `config.yaml`:

```yaml
dsar:
  enabled: true
  require_admin_review: true
  default_format: "json"
  export_formats:
    - json
    - zip
  max_export_size_mb: 500
  retention_days: 7
  pending_expiry_days: 30
  audit_log:
    file_path: "~/.plexichat/data/dsar_audit_log.jsonl"
    hash_chain_enabled: true
    halt_on_invalid_audit: true
  harvester:
    interval_hours: 24
    boot_check_enabled: true
    batch_size: 20
```

### Configuration options

| Option | Type | Default | Description |
|---|---|---|---|
| `enabled` | boolean | `true` | Enable or disable the entire DSAR system. |
| `require_admin_review` | boolean | `true` | Require admin approval before the harvester processes a request. If `false`, pending requests are auto-approved. |
| `default_format` | string | `"json"` | Format to use when the user does not specify one. |
| `export_formats` | array | `["json", "zip"]` | Allowed export formats. |
| `max_export_size_mb` | integer | `500` | Approximate maximum size of a generated export. |
| `retention_days` | integer | `7` | Days a download link remains valid after the export is ready. |
| `pending_expiry_days` | integer | `30` | Days a pending request survives without action before it expires. |
| `harvester.interval_hours` | integer | `24` | How often the harvester checks for work, in hours. |
| `harvester.boot_check_enabled` | boolean | `true` | Run a harvester and audit-log integrity check at server startup. |
| `harvester.batch_size` | integer | `20` | How many approved requests to process per batch, oldest first. |
| `audit_log.file_path` | string | `"~/.plexichat/data/dsar_audit_log.jsonl"` | Path to the hash-chained DSAR audit log. |
| `audit_log.hash_chain_enabled` | boolean | `true` | Include the hash of the previous entry in each audit record. |
| `audit_log.halt_on_invalid_audit` | boolean | `true` | Halt the harvester at startup if hash-chain verification fails. |

## Use the user-facing API

### Request an export

```http
POST /api/v1/users/@me/data-export
Content-Type: application/json

{
  "password": "user_password",
  "format": "json"
}
```

Response:

```json
{
  "success": true,
  "message": "Data export request submitted successfully"
}
```

The request body requires password re-verification. The optional `format` must be one of `dsar.export_formats`.

### List your export requests

```http
GET /api/v1/users/@me/data-export
```

Response:

```json
{
  "requests": [
    {
      "id": 12345,
      "status": "ready",
      "requested_at": 1704067200000,
      "completed_at": 1704070800000,
      "expires_at": 1704675600000,
      "format": "json",
      "file_size_bytes": 1048576,
      "checksum": "sha256:abc123..."
    }
  ]
}
```

### Get request status

```http
GET /api/v1/users/@me/data-export/{request_id}
```

### Download an export

```http
GET /api/v1/users/@me/data-export/{request_id}/download
```

The server returns `400` if the export is not `ready` and `410` if the link has expired.

### Cancel a request

```http
DELETE /api/v1/users/@me/data-export/{request_id}
```

You can cancel a request while it is `pending` or `approved`. You cannot cancel it once it is `generating` or `ready`.

## Use the admin API

All admin DSAR endpoints are defined in `src/api/routes/admin/data_export.py:6`. Required permissions are `data_export.read` or `data_export.process`.

### List all requests

```http
GET /api/v1/admin/data-export?status=pending&limit=50&offset=0
```

Query parameters:

| Parameter | Type | Description |
|---|---|---|
| `status` | string | Filter by `pending`, `approved`, `generating`, `ready`, `failed`, `expired`, or `cancelled`. |
| `limit` | integer | Maximum results to return. Default `50`. |
| `offset` | integer | Pagination offset. Default `0`. |

Response:

```json
{
  "items": [
    {
      "id": 12345,
      "user_id": 1001,
      "status": "pending",
      "requested_at": 1704067200000,
      "completed_at": null,
      "expires_at": null,
      "format": "json",
      "file_size_bytes": null
    }
  ],
  "has_more": false
}
```

Note: Rows from `dsar_requests` are never serialized verbatim. Columns such as `storage_path` are excluded by `REQUEST_RESPONSE_COLUMNS` in `src/api/routes/admin/data_export.py:38` to avoid leaking filesystem layout.

### Get request details

```http
GET /api/v1/admin/data-export/{request_id}
```

Requires `data_export.read`.

### Approve a request

```http
POST /api/v1/admin/data-export/{request_id}/approve
```

Requires `data_export.process`.

### Deny a request

```http
POST /api/v1/admin/data-export/{request_id}/deny
Content-Type: application/json

{
  "reason": "Reason for denial"
}
```

Requires `data_export.process`. `reason` must be 1 to 500 characters.

### Generate an export manually

```http
POST /api/v1/admin/data-export/{request_id}/generate
```

Requires `data_export.process`. Use this when the harvester is disabled or you need immediate processing. It runs `generate_manual` in a worker thread. See `src/api/routes/admin/data_export.py:177`.

## Understand request statuses

| Status | Description |
|---|---|
| `pending` | Awaiting admin approval, if `require_admin_review` is `true`. |
| `approved` | Approved and waiting for the harvester or a manual generate call. |
| `generating` | The harvester is building the export file. |
| `ready` | The file is ready for download until `expires_at`. |
| `failed` | Generation failed. Check `error_message`. |
| `expired` | The download window elapsed. |
| `cancelled` | The user cancelled the request. |

## Understand the harvester worker

The harvester is analogous to the account reaper but for exports.

1. **Startup check.** If `harvester.boot_check_enabled` is `true`, the worker verifies the DSAR audit log hash chain at startup. If verification fails and `halt_on_invalid_audit` is `true`, the worker halts.
2. **Batch selection.** The worker selects the oldest `approved` rows first, up to `harvester.batch_size`. If `require_admin_review` is `false`, it auto-approves `pending` rows.
3. **Data collection.** For each request, the worker sets `status = 'generating'`, collects data through the DSAR collector, writes the file (JSON or ZIP), stores `file_size_bytes` and `checksum`, and sets `status = 'ready'` with `expires_at = now + retention_days`.
4. **Cleanup.** Rows that are `ready` but past `expires_at` transition to `expired`.
5. **Error handling.** A failure sets `status = 'failed'` with an `error_message`. The worker continues to the next request.

## Follow security best practices

### Authentication and authorization

* Require password re-verification for every user request. This prevents extraction from a briefly unattended session.
* Enforce the built-in rate limit of one request per 24 hours per user.
* Grant `data_export.read` and `data_export.process` only to admins who need them.

### Download link security

* Links expire after `retention_days`. Default is 7 days.
* Export file paths are stored as plaintext in the `storage_path` column and are not encrypted at rest. Access control on the database and download endpoints protects them. At-rest encryption for export files is available only if `media.encrypt_at_rest` wrapping is enabled at the storage layer.
* Each export includes a SHA-256 checksum for integrity verification.

### Audit logging

The DSAR system maintains a hash-chained audit log at `dsar.audit_log.file_path`:

* Each entry includes the hash of the previous entry, so tampering is detectable.
* Logged events include `REQUESTED`, `APPROVED`, `DENIED`, `GENERATING`, `READY`, `FAILED`, `DOWNLOADED`, and `EXPIRED`.
* There is no automated S3 backup of this file. Back up `~/.plexichat/data/dsar_audit_log.jsonl` yourself if you need redundancy.
* If hash-chain verification fails at startup and `halt_on_invalid_audit` is `true`, the harvester does not start. Investigate before you set `halt_on_invalid_audit` to `false`.

## Troubleshoot data export

### The user cannot submit a request

* Verify `dsar.enabled` is `true`.
* Check whether the user submitted a request within the last 24 hours.
* Confirm the password is correct and password verification is not failing due to account lock.

### Exports are not processing

* Check server logs to verify the harvester is running.
* Verify `dsar.harvester.interval_hours` is set as expected.
* If `require_admin_review` is `true`, verify the request is `approved`. Pending requests do not run.
* Look for `failed` requests in the admin panel and inspect `error_message`.

### The download link expired

* Increase `dsar.retention_days` if users need a longer window.
* Call `POST /api/v1/admin/data-export/{request_id}/generate` to build a new export after you approve the request again, if applicable.

### The harvester halted on audit error

* Do not set `halt_on_invalid_audit` to `false` until you investigate.
* Check the integrity of `dsar.audit_log.file_path`.
* Restore your own backup of the audit log if you keep one. No automated backup exists.
* After you resolve the issue, restart the server to let the harvester run again.

## Related documentation

* [RBAC](rbac.md) — Permissions `data_export.read` and `data_export.process`.
* [Audit logging](audit-logging.md) — General admin audit trail in `admin_audit_log`.
* [Configuration overview](../configuration.md) — Where to set the `dsar` block.
