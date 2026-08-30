# Reports API

Report messages or users for moderation. You use this API to flag content or behavior, and admins use it to review and resolve reports.

All endpoints in this document are relative to `https://api.plexichat.com`.
For local development, use `http://localhost:8000`.

You must authenticate with `Authorization: Bearer <token>` for all report endpoints.

## Rate limiting and privacy

*   You can submit up to 10 reports per rolling minute. If you exceed the limit, the API returns `429` or `503` depending on the backend tier. The limit is enforced atomically in the shared cache.
*   A reporter cannot report their own message or their own account. The API returns `400`.
*   Reporting an already-pending target is idempotent. Instead of creating a duplicate moderation entry, the API returns the existing open report with `200` and a message such as `Report already submitted`.
*   Report submission does not imply immediate moderation action. Show a privacy-conscious success message and avoid polling for a visible outcome.

## Report categories

All report endpoints accept a `category`. Valid values are `harassment`, `spam`, `inappropriate`, `illegal`, `hate_speech`, `threats`, `impersonation`, and `other`. Category names are case-sensitive and use lowercase.

Enhanced reports also accept a `priority` of `low`, `medium`, `high`, or `critical`.

## Report a message

Report a message for content moderation.

```
POST /api/v1/reports/messages
```

The API validates the message ID format and checks that the message is accessible to you. If the messaging backend is available and the message cannot be found or viewed, the API returns `400 Message not found or not accessible`. This prevents fabricated reports against arbitrary IDs.

If the message has attachments, the backend may also create hash-based media reports so the moderation system can block the file itself.

### Request body

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `message_id` | string | Yes | Snowflake ID of the message to report. |
| `channel_id` | string | Yes | Snowflake ID of the channel that contains the message. |
| `server_id` | string | No | Snowflake ID of the server, if applicable. |
| `reason` | string | Yes | Brief reason. `1`–`500` characters. |
| `category` | string | No | Report category. Default `other`. Must be a valid category. |
| `details` | string | No | Additional details. Max `2000` characters. |

### Example request

```bash
curl -X POST https://api.plexichat.com/api/v1/reports/messages \
  -H "Authorization: Bearer YOUR_SESSION_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "message_id": "123456789012345678",
    "channel_id": "234567890123456789",
    "reason": "Spam",
    "category": "spam",
    "details": "Repeated unsolicited links"
  }'
```

### Response

`200 OK`

```json
{
  "success": true,
  "report_id": "123456789012345679",
  "message": "Report submitted successfully"
}
```

If an identical pending report already exists:

```json
{
  "success": true,
  "report_id": "123456789012345679",
  "message": "Report already submitted"
}
```

| Field | Type | Description |
| --- | --- | --- |
| `success` | boolean | Whether the report was accepted. |
| `report_id` | string | Snowflake ID of the report. |
| `message` | string | Human-readable status message. |

### Errors

| Status | Description |
| --- | --- |
| `400` | Invalid ID format, invalid category, cannot report your own message, or message not accessible. |
| `401` | Not authenticated. |
| `429` | Too many reports submitted. Slow down. |
| `503` | Report submission temporarily unavailable (rate-limit backend failure). |

## Report a user

Report a user for behavior issues.

```
POST /api/v1/reports/users
```

The API verifies that the reported user exists and that you are not reporting yourself.

### Request body

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `user_id` | string | Yes | Snowflake ID of the user to report. |
| `reason` | string | Yes | Brief reason. `1`–`500` characters. |
| `category` | string | No | Report category. Default `other`. |
| `details` | string | No | Additional details. Max `2000` characters. |
| `evidence_message_ids` | array of strings | No | Message IDs that support the report. Each must be a valid Snowflake ID. |

### Example request

```bash
curl -X POST https://api.plexichat.com/api/v1/reports/users \
  -H "Authorization: Bearer YOUR_SESSION_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "234567890123456789",
    "reason": "Harassment",
    "category": "harassment",
    "evidence_message_ids": ["345678901234567890"]
  }'
```

### Response

`200 OK`

```json
{
  "success": true,
  "report_id": "123456789012345680",
  "message": "Report submitted successfully"
}
```

### Errors

| Status | Description |
| --- | --- |
| `400` | Invalid user ID, invalid category, cannot report yourself, reported user not found, or invalid evidence message ID. |
| `401` | Not authenticated. |
| `429` | Too many reports submitted. Slow down. |
| `503` | Report submission temporarily unavailable. |

## Submit an enhanced report

Submit an enhanced report that supports both `message` and `user` targets, priority, and evidence URLs. You use this when you need richer moderation metadata than the legacy message or user routes provide.

```
POST /api/v1/reports/enhanced
```

Evidence URLs are validated as public `http` or `https` URLs. The server rejects URLs with credentials, loopback, link-local, private, multicast, reserved, or unspecified addresses to prevent SSRF (the URL may later be fetched or rendered by moderation tools).

### Request body

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `target_type` | string | Yes | `message` or `user`. |
| `target_id` | string | Yes | Snowflake ID of the reported message or user. |
| `reason` | string | Yes | Reason. `1`–`1000` characters. |
| `category` | string | No | Report category. Default `other`. |
| `priority` | string | No | `low`, `medium`, `high`, or `critical`. Default `medium`. |
| `details` | string | No | Additional details. Max `2000` characters. |
| `evidence_urls` | array of strings | No | Public evidence URLs (screenshots, etc.). Each must be a safe `http` or `https` URL. |
| `channel_id` | string | No | Channel ID, required for message reports. |
| `server_id` | string | No | Server ID, for message reports. |

### Example request

```bash
curl -X POST https://api.plexichat.com/api/v1/reports/enhanced \
  -H "Authorization: Bearer YOUR_SESSION_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "target_type": "message",
    "target_id": "123456789012345678",
    "reason": "Hate speech in message",
    "category": "hate_speech",
    "priority": "high",
    "channel_id": "234567890123456789",
    "evidence_urls": ["https://example.com/screenshot.png"]
  }'
```

### Response

`200 OK`

```json
{
  "success": true,
  "report_id": "123456789012345681",
  "status": "open",
  "priority": "high"
}
```

If a report for the same reporter, type, and target is already open or under investigation:

```json
{
  "success": true,
  "report_id": "123456789012345681",
  "status": "open",
  "priority": "high",
  "message": "Report already submitted"
}
```

Duplicate protection is enforced by a partial unique database index on `(reporter_id, report_type, target_id)` where `status` is `open` or `investigating`. Concurrent requests that race the check return the winning report rather than a database error.

### Errors

| Status | Description |
| --- | --- |
| `400` | Invalid `target_type`, invalid category or priority, invalid evidence URL, invalid ID format, cannot report your own content, or message/user not found. |
| `401` | Not authenticated. |
| `429` | Too many reports. Slow down. |
| `503` | Report submission temporarily unavailable. |

## Update report status (admin only)

Update the lifecycle status of a report. Only admins can call this endpoint.

```
PATCH /api/v1/reports/{report_id}/status
```

### Path parameters

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| `report_id` | string | Yes | Snowflake ID of the report. |

### Request body

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `status` | string | Yes | New status: `open`, `investigating`, `resolved`, `dismissed`, or `escalated`. |
| `priority` | string | No | Updated priority. |
| `assigned_to` | string | No | Snowflake ID of the admin to assign. |
| `admin_notes` | string | No | Admin notes. Max `2000` characters. |
| `resolution` | string | No | Resolution description. Max `2000` characters. |

Only fields you include are updated. The API uses a static, parameterized `UPDATE` with `COALESCE` for each column, so omitted fields keep their current value. Do not build column names from user input.

Reopening a report (`status` set to `open`) clears prior review and resolution metadata (`resolution`, `resolved_at`, `resolved_by`, `reviewed_at`, `reviewed_by`). Every other transition keeps or sets lifecycle fields: `resolution` sets `resolved_at` and `resolved_by`; `investigating`, `resolved`, `dismissed`, or `escalated` sets `reviewed_at` and `reviewed_by`; `escalated` sets `escalated_at`.

### Example request

```bash
curl -X PATCH https://api.plexichat.com/api/v1/reports/123456789012345681/status \
  -H "Authorization: Bearer YOUR_SESSION_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "status": "investigating",
    "priority": "high",
    "assigned_to": "234567890123456789",
    "admin_notes": "Reviewing evidence"
  }'
```

### Response

`200 OK`

```json
{
  "success": true,
  "report_id": "123456789012345681",
  "status": "investigating"
}
```

### Errors

| Status | Description |
| --- | --- |
| `400` | Invalid report ID or status. |
| `401` | Not authenticated. |
| `403` | Admin access required. |
| `500` | Database not available or internal error. |

## Get report details

Get details for a single report. You can view a report if you are the reporter or an admin. Reporters receive a safe projection that omits internal moderation fields.

```
GET /api/v1/reports/{report_id}
```

### Path parameters

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| `report_id` | integer | Yes | Snowflake ID of the report. The API checks the unified `reports` table and falls back to `message_reports` and `user_reports` legacy tables. |

### Example request

```bash
curl -X GET https://api.plexichat.com/api/v1/reports/123456789012345681 \
  -H "Authorization: Bearer YOUR_SESSION_TOKEN"
```

### Response

`200 OK`

```json
{
  "report": {
    "id": "123456789012345681",
    "reporter_id": "123456789012345678",
    "report_type": "message",
    "target_id": "123456789012345678",
    "reason": "Spam",
    "status": "open",
    "priority": "medium",
    "created_at": 1704067200000
  }
}
```

Reporters do not receive admin-only fields such as `admin_notes`, `reviewed_by`, `reviewed_at`, `action_taken`, `resolution`, `resolved_by`, `assigned_to`, or `escalated_at`. Admins receive the full record.

### Errors

| Status | Description |
| --- | --- |
| `401` | Not authenticated. |
| `403` | You are not the reporter and you are not an admin. |
| `404` | Report not found. |

## Related endpoints

*   [Messages](messages.md) — Message contents and moderation context.
*   [Admin](../admin/overview.md) — Admin review workflows.
