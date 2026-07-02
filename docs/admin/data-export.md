# Data Export (DSAR)

Plexichat provides comprehensive Data Subject Access Request (DSAR) functionality, implementing GDPR Article 20 - Right to Portability. This allows users to request a complete export of their personal data.

## Overview

The DSAR system enables users to request and download all their personal data in a machine-readable format. The system supports both JSON and ZIP export formats, with a complete audit trail and admin oversight throughout the process.

## Workflow

```
User Request → Admin Review → Harvester Processing → User Download
     │              │                │                  │
     ▼              ▼                ▼                  ▼
  Password       Approve/         Generate         Download
  verified       Deny              Export            Link
```

### Step 1: User Request
Users submit a data export request via their account settings. The request requires password verification to ensure the user is authenticated and authorized to request the data.

### Step 2: Admin Review
Admins review pending requests in the Admin Panel under Data Export. They can:
- View request details and history
- Approve or deny requests (if `require_admin_review` is enabled)
- Manually trigger export generation

### Step 3: Harvester Processing
The DSAR Harvester is a background worker that:
- Processes approved requests in batches
- Collects all user data from the database
- Generates export files in the requested format
- Updates request status and prepares for download

### Step 4: User Download
Once the export is ready, users receive a download link valid for the configured retention period (default: 7 days).

## Configuration

DSAR is configured in the `dsar` section of the configuration file:

```yaml
dsar:
  # Enable/disable the DSAR system
  enabled: true

  # Require admin review before generating exports
  require_admin_review: true

  # Default export format when not specified
  default_format: "json"

  # Supported export formats
  export_formats:
    - json
    - zip

  # Maximum export size in MB (approximate)
  max_export_size_mb: 500

  # How long download links remain valid (days)
  retention_days: 7

  # How long pending requests survive without action (days)
  pending_expiry_days: 30

  # Audit logging configuration
  audit_log:
    file_path: "~/.plexichat/data/dsar_audit_log.jsonl"
    hash_chain_enabled: true
    backup_to_s3: true
    s3_backup_path: "audit/dsar/log_backup.jsonl"
    halt_on_invalid_audit: true

  # Harvester worker configuration
  harvester:
    # How often the harvester runs (hours)
    interval_hours: 24
    # Run harvester check on server startup
    boot_check_enabled: true
    # Number of requests to process per batch
    batch_size: 20
```

### Configuration Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `enabled` | boolean | `true` | Enable/disable the entire DSAR system |
| `require_admin_review` | boolean | `true` | Require admin approval before export generation |
| `default_format` | string | `"json"` | Default export format when user doesn't specify |
| `export_formats` | array | `["json", "zip"]` | Supported export format options |
| `max_export_size_mb` | integer | `500` | Approximate maximum export size |
| `retention_days` | integer | `7` | Days until download links expire |
| `pending_expiry_days` | integer | `30` | Days until unprocessed requests expire |
| `harvester.interval_hours` | integer | `24` | Harvester check frequency |
| `harvester.boot_check_enabled` | boolean | `true` | Run harvester on server startup |
| `harvester.batch_size` | integer | `20` | Requests per harvester batch |

## User-Facing API Endpoints

### Request Data Export
```
POST /api/v1/users/@me/data-export
```
Submit a new data export request. Requires password verification.

**Request Body:**
```json
{
  "password": "user_password",
  "format": "json"
}
```

**Response:**
```json
{
  "success": true,
  "message": "Data export request submitted successfully"
}
```

### List Export Requests
```
GET /api/v1/users/@me/data-export
```
Get all data export requests for the current user.

**Response:**
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

### Get Request Status
```
GET /api/v1/users/@me/data-export/{request_id}
```
Get the status of a specific export request.

### Download Export
```
GET /api/v1/users/@me/data-export/{request_id}/download
```
Get the download URL for a ready export. Returns 400 if not ready, 410 if expired.

### Cancel Request
```
DELETE /api/v1/users/@me/data-export/{request_id}
```
Cancel a pending or approved request. Cannot cancel if already generating or ready.

## Admin API Endpoints

### List All Requests
```
GET /api/v1/admin/data-export?status=pending&limit=50&offset=0
```
List all DSAR requests with optional filtering. Requires `data_export.read` permission.

**Query Parameters:**
- `status`: Filter by status (`pending`, `approved`, `generating`, `ready`, `failed`, `expired`, `cancelled`)
- `limit`: Maximum number of results (default: 50)
- `offset`: Pagination offset

**Response:**
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
      "file_size_bytes": null,
      "checksum": null
    }
  ],
  "has_more": false,
  "total": null
}
```

### Get Request Details
```
GET /api/v1/admin/data-export/{request_id}
```
View detailed information about a specific request. Requires `data_export.read` permission.

### Approve Request
```
POST /api/v1/admin/data-export/{request_id}/approve
```
Approve a pending DSAR request, allowing the harvester to process it. Requires `data_export.process` permission.

### Deny Request
```
POST /api/v1/admin/data-export/{request_id}/deny
```
Deny a pending request. Requires `data_export.process` permission.

**Request Body:**
```json
{
  "reason": "Reason for denial"
}
```

### Generate Export
```
POST /api/v1/admin/data-export/{request_id}/generate
```
Manually trigger export generation for an approved request. Useful if the harvester is disabled or for immediate processing. Requires `data_export.process` permission.

## Request Statuses

| Status | Description |
|--------|-------------|
| `pending` | Awaiting admin approval (if required) |
| `approved` | Approved, awaiting harvester processing |
| `generating` | Export file being generated |
| `ready` | Export available for download |
| `failed` | Export generation failed |
| `expired` | Download link has expired |
| `cancelled` | Request cancelled by user |

## Harvester Worker

The DSAR Harvester is a background worker analogous to the Account Reaper but for data exports instead of deletions.

### Processing Logic

1. **Startup Check**: On server startup (if `boot_check_enabled`), the harvester verifies the audit log integrity and halts if the chain is invalid (configurable via `halt_on_invalid_audit`).

2. **Batch Processing**: The harvester processes requests in batches of `batch_size`, selecting the oldest approved requests first.

3. **Admin Review Mode**: If `require_admin_review` is true, only `approved` requests are processed. If false, pending requests are auto-approved.

4. **Data Collection**: For each request, the harvester:
   - Updates status to `generating`
   - Collects all user data via the DataCollector
   - Generates export file (JSON or ZIP)
   - Updates status to `ready` with expiration time

5. **Cleanup**: Expired requests (status `ready` with expired `expires_at`) are marked as `expired`.

### Error Handling

Failed requests are marked with status `failed` and the error message stored. The harvester continues processing remaining requests even if one fails.

## Security Considerations

### Authentication and Authorization

1. **Password Re-verification**: Users must provide their password when requesting a data export. This prevents unauthorized data extraction if someone gains temporary access to an authenticated session.

2. **Rate Limiting**: Users are limited to 1 request per 24 hours to prevent abuse.

3. **Admin Permissions**: Admin operations require specific permissions:
   - `data_export.read`: View requests and details
   - `data_export.process`: Approve, deny, generate exports

### Download Link Security

1. **Time-Limited Links**: Download links expire after `retention_days` (default: 7 days).

2. **File Path Encryption**: Export file paths are stored encrypted in the database.

3. **Checksum Verification**: Each export includes a SHA-256 checksum for integrity verification.

### Audit Logging

The DSAR system maintains a hash-chained audit log for compliance:

1. **Hash Chain**: Each audit entry includes the hash of the previous entry, making tampering detectable.

2. **Logged Events**: All state transitions are logged:
   - `REQUESTED`: User submitted a request
   - `APPROVED`: Admin approved the request
   - `DENIED`: Admin denied the request
   - `GENERATING`: Harvester started processing
   - `READY`: Export file generated
   - `FAILED`: Generation failed
   - `DOWNLOADED`: User downloaded the export
   - `EXPIRED`: Download link expired

3. **S3 Backup**: Audit logs can be automatically backed up to S3 for redundancy.

4. **Integrity Halt**: If hash chain verification fails at startup and `halt_on_invalid_audit` is true, the harvester will not start.

### Admin Oversight

1. **Manual Generation**: Admins can manually trigger export generation if the automated harvester is delayed or disabled.

2. **Request Denial**: Admins can deny requests with a reason, providing accountability.

3. **Complete Visibility**: Admins can view all requests across all users for compliance reporting.

## Best Practices

1. **Enable Admin Review**: Keep `require_admin_review` enabled for production deployments to maintain oversight.

2. **Monitor Pending Queue**: Regularly check for pending requests that may be approaching `pending_expiry_days`.

3. **Review Audit Logs**: Periodically review the DSAR audit log for any anomalies.

4. **Set Appropriate Retention**: Balance user convenience with storage costs when setting `retention_days`.

5. **Backup Audit Logs**: Ensure `backup_to_s3` is enabled for compliance and disaster recovery.

6. **Configure Alerting**: Set up monitoring for the harvester to detect processing failures or shutdowns.

## Troubleshooting

### User Cannot Request Export
- Verify DSAR is enabled: `dsar.enabled: true`
- Check rate limiting: User may have made a request within 24 hours
- Confirm password verification is working

### Exports Not Processing
- Verify harvester is running in server logs
- Check `dsar.harvester.interval_hours` configuration
- If `require_admin_review` is true, ensure requests are being approved
- Check for failed requests in the admin panel

### Download Link Expired
- Increase `dsar.retention_days` for longer download windows
- Admin can use Generate endpoint to create a new export

### Harvester Halted on Audit Error
- This is a critical security feature preventing potential data tampering
- Investigate the audit log integrity issue
- Restore from S3 backup if available
- After resolution, set `halt_on_invalid_audit: false` to restart (only after thorough investigation)