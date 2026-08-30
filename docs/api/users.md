# Users API

Manage user profiles, Direct Messages, and GDPR data exports.

**Base URL**: `https://api.plexichat.com/api/v1`

For development, use `http://localhost:8000/api/v1`.

All endpoints in this document are mounted under `/users` (`plexichat/src/api/routes/__init__.py:65`). Full paths are shown as `/api/v1/users/...`.

All endpoints require `Authorization: Bearer <token>` unless noted.

## Get the current user

```
GET /api/v1/users/@me
```

Returns the private profile for the authenticated user or bot. This endpoint is cached for 60 seconds.

### Response

`200 OK` (`UserResponse` in `plexichat/src/api/schemas/auth.py`)

```json
{
  "id": "123456789012345678",
  "username": "johndoe",
  "email": "john@example.com",
  "avatar_url": "https://api.plexichat.com/avatars/users/123456789012345678",
  "created_at": 1704067200,
  "email_verified": true,
  "totp_enabled": false,
  "age_verified": false,
  "badges": ["early_supporter"],
  "deletion_status": "active",
  "deletion_at": null
}
```

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Snowflake ID. |
| `username` | string | Username. |
| `email` | string? | Email (private). `null` for bots. |
| `avatar_url` | string? | Avatar URL or `null`. |
| `created_at` | integer | Unix timestamp of account creation. |
| `email_verified` | boolean | Whether the email is verified. |
| `totp_enabled` | boolean | Whether 2FA is enabled. |
| `age_verified` | boolean | Whether age is verified. |
| `badges` | array | Badge identifiers. |
| `deletion_status` | string | Account deletion state. |
| `deletion_at` | integer? | Deletion scheduling timestamp. |

Bots are returned with `email: null`, `email_verified: false`, and `badges: []` (`plexichat/src/api/routes/users/profile.py:44`).

### Errors

| Status | Condition |
|--------|-----------|
| `401` | Missing or invalid token. |
| `404` | User or bot not found. |
| `500` | Auth module unavailable or internal error. |

## Update the current user

```
PATCH /api/v1/users/@me
```

Updates the current user's profile.

### Request body

`UserUpdateRequest` — all fields are optional.

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| `username` | string | 3–32 characters | New username. Must be unique. |
| `email` | string | Valid email | New email. Requires current password (see below). |
| `password` | string | Min 8 characters, must meet password policy | New password. Requires `current_password`. |
| `current_password` | string | — | Required when changing `password` or `email`. |
| `bio` | string | — | Profile bio. |
| `pronouns` | string | — | Pronouns. |
| `location` | string | — | Location. |
| `timezone` | string | — | Timezone. |
| `banner_url` | string | — | Banner URL. |
| `social_links` | object | — | Social link map. |
| `avatar_url` | string | Must be `/api/v1/avatars/users/{user_id}` and match an uploaded avatar | Change avatar reference (upload the image first with `POST /users/@me/avatar`). |

#### Password and email changes

- To change `password`, you must send `current_password`. The server calls `auth.change_password` and returns `403 Incorrect current password` on failure. The `password` fields are removed before the generic profile update.
- To change `email`, you must also send `current_password` unless you already verified a password in the same request (for example, when changing both `password` and `email` together). The server verifies with `auth.verify_user_password` and the auth manager resets `email_verified` to `false` so the new address must be verified (`plexichat/src/api/routes/users/profile.py:145`).

#### Avatar URL validation

`avatar_url` is not a free-form URL. It must be the canonical path `/api/v1/avatars/users/{user_id}` and the user must have an uploaded avatar row (`avatars.get_user_avatar`). Arbitrary external URLs are rejected with `400` (`plexichat/src/api/routes/users/profile.py:300`).

### Example request

```bash
curl -X PATCH http://localhost:8000/api/v1/users/@me \
  -H "Authorization: Bearer YOUR_SESSION_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"username":"newusername"}'
```

### Response

`200 OK` — the updated private user object.

On success, the server invalidates `user_data:*{user_id}*` and emits a gateway event (`emit_user_update`) to other sessions.

### Errors

| Status | Condition |
|--------|-----------|
| `400` | Invalid `username`, `email`, or `avatar_url`; or invalid profile field value. |
| `400` | Missing `current_password` when changing `password` or `email`. |
| `403` | Incorrect `current_password`. |
| `409` | Username or email already in use (`UserExistsError`). |
| `404` | User not found. |
| `500` | Internal error. |

## Get messaging settings

```
GET /api/v1/users/@me/messaging-settings
```

Returns messaging preferences for the current user.

### Response

`200 OK` (`MessagingSettingsResponse`)

```json
{
  "user_id": "123456789012345678",
  "read_receipts_enabled": true,
  "typing_indicators_enabled": true,
  "compact_messages_enabled": true,
  "allow_dms_from": "everyone",
  "auto_create_dms": true,
  "max_message_length": null,
  "max_attachment_size": null,
  "max_attachments_per_message": null
}
```

## Update messaging settings

```
PATCH /api/v1/users/@me/messaging-settings
```

Updates messaging preferences. All fields are optional.

| Field | Type | Allowed values |
|-------|------|----------------|
| `read_receipts_enabled` | boolean | `true`, `false` |
| `typing_indicators_enabled` | boolean | `true`, `false` |
| `compact_messages_enabled` | boolean | `true`, `false` |
| `allow_dms_from` | string | `everyone`, `friends`, `none` |
| `auto_create_dms` | boolean | `true`, `false` |

```json
{
  "read_receipts_enabled": false,
  "allow_dms_from": "friends"
}
```

**Response**: `200 OK` with the updated messaging settings.

## Upload an avatar

```
POST /api/v1/users/@me/avatar
```

Uploads a new avatar image.

```bash
curl -X POST http://localhost:8000/api/v1/users/@me/avatar \
  -H "Authorization: Bearer YOUR_SESSION_TOKEN" \
  -F "file=@/path/to/avatar.png"
```

### Response

`200 OK`

```json
{
  "success": true,
  "avatar_url": "https://api.plexichat.com/avatars/users/123456789012345678",
  "width": 256,
  "height": 256,
  "size": 12345,
  "animated": false
}
```

### Errors

| Status | Condition |
|--------|-----------|
| `400` | Invalid file type or file too large. |
| `401` | Not authenticated. |
| `500` | Internal error. |

## Notes channel

```
GET /api/v1/users/@me/notes
```

Gets or creates the personal notes channel — a single-user conversation that syncs across devices.

**Response** `200 OK`

```json
{
  "id": "123456789012345678",
  "type": "notes",
  "name": "Personal Notes",
  "last_message_id": "234567890123456789",
  "last_message_at": 1704067200
}
```

## DM channels

### List DM channels

```
GET /api/v1/users/@me/channels
```

Returns all DM channels for the current user.

**Response** `200 OK` (`DMChannelResponse[]`)

```json
[
  {
    "id": "123456789012345678",
    "type": "dm",
    "recipient_id": "234567890123456789",
    "recipient": {
      "id": "234567890123456789",
      "username": "janedoe"
    },
    "last_message_id": "345678901234567890"
  }
]
```

### Create a DM channel

```
POST /api/v1/users/@me/channels
```

Creates or returns an existing DM channel with another user.

**Request body**:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `recipient_id` | string | Yes | Target user's snowflake ID. |

```json
{
  "recipient_id": "234567890123456789"
}
```

**Response** `200 OK` with the `DMChannelResponse`.

**Errors**:

| Status | Condition |
|--------|-----------|
| `400` | Missing or invalid `recipient_id`. |
| `403` | Cannot message this user (blocked). |
| `404` | Recipient not found. |

## Search for a user

```
GET /api/v1/users/search?username=johndoe
```

Searches by exact username (case-insensitive).

| Query parameter | Type | Required | Description |
|-----------------|------|----------|-------------|
| `username` | string | Yes | Username to search for. |

```bash
curl -X GET "http://localhost:8000/api/v1/users/search?username=johndoe" \
  -H "Authorization: Bearer YOUR_SESSION_TOKEN"
```

**Response** `200 OK`

```json
{
  "id": "123456789012345678",
  "username": "johndoe",
  "avatar_url": "https://api.plexichat.com/avatars/users/123456789012345678",
  "created_at": 1704067200
}
```

**Errors**:

| Status | Condition |
|--------|-----------|
| `400` | Missing `username` or invalid format. |
| `401` | Not authenticated. |
| `404` | No user with that username. |

## Get a user by ID

```
GET /api/v1/users/{user_id}
```

Returns the public profile for any user. Cached for 60 seconds.

| Path parameter | Type | Description |
|----------------|------|-------------|
| `user_id` | string | Snowflake ID. Non-numeric or out-of-range IDs return `400` or `404`. |

**Response** `200 OK` (`UserPublicResponse`)

```json
{
  "id": "123456789012345678",
  "username": "johndoe",
  "avatar_url": "https://cdn.example.com/avatars/123.png",
  "created_at": 1704067200,
  "badges": []
}
```

**Errors**:

| Status | Condition |
|--------|-----------|
| `400` | Invalid user ID format. |
| `404` | User not found or ID out of 64-bit range. |

## Schedule account deletion

```
DELETE /api/v1/users/@me
```

Schedules the current account for deletion. Requires password and optionally a TOTP code.

**Request body** (`UserDeletionRequest`):

```json
{
  "password": "correct horse battery staple",
  "code": "123456"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `password` | string | Yes | Current password. |
| `code` | string | No | TOTP code when 2FA is enabled. |

**Response** `200 OK`

```json
{
  "success": true
}
```

**Errors**:

| Status | Condition |
|--------|-----------|
| `400` | Invalid password or 2FA code. |
| `401` | Not authenticated. |
| `500` | Internal error. |

## Data exports (DSAR - GDPR Article 20)

Endpoints for the right to data portability. All paths are under `/users/@me/data-export` (`plexichat/src/api/routes/users/composer.py:182`). Each request is rate-limited to one per 24 hours per user.

The server verifies the password by checking the `password_hash` directly from `auth_users` when the `User` object doesn't carry it, so the verification works without a prior login in the same process (`plexichat/src/api/routes/users/dsar.py:50`). Password verification uses `src.utils.encryption.verify_password`.

### Request a data export

```
POST /api/v1/users/@me/data-export
```

Starts a new export job.

**Request body** (`DSARRequestBody` in `plexichat/src/api/schemas/dsar.py:9`):

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `password` | string | Yes | Current password for verification. |
| `format` | string | No | `json` (default) or `zip`. |

```json
{
  "password": "correct horse battery staple",
  "format": "zip"
}
```

**Behavior**:

1. Verifies the password. On failure, returns `403 Incorrect password`.
2. Checks the rate limit with key `dsar:user:{user_id}` — one request per 86,400 seconds (`plexichat/src/api/routes/users/dsar.py:75`). If exceeded, returns `429` with `Data export request limit exceeded. Please wait 24 hours between requests.`
3. Calls `dsar.request_data_export(user_id, format)` and logs the request.

**Response** `200 OK`

```json
{
  "success": true,
  "message": "Data export request submitted successfully"
}
```

**Errors**:

| Status | Condition |
|--------|-----------|
| `400` | Invalid request body. |
| `401` | Not authenticated. |
| `403` | Incorrect password. |
| `429` | Rate limit exceeded (1 per 24 hours). |
| `500` | Auth module unavailable or internal error. |

### List data export requests

```
GET /api/v1/users/@me/data-export
```

Lists all export requests for the current user.

**Response** `200 OK` (`DSARRequestListResponse`)

```json
{
  "requests": [
    {
      "id": 1,
      "status": "ready",
      "requested_at": 1704067200,
      "completed_at": 1704070800,
      "expires_at": 1704675600,
      "format": "json",
      "file_size_bytes": 123456,
      "checksum": "a1b2c3..."
    }
  ]
}
```

| Field | Type | Description |
|-------|------|-------------|
| `id` | integer | Request ID. |
| `status` | string | `pending`, `approved`, `generating`, `ready`, `downloaded`, `cancelled`, `expired`, `failed`. |
| `requested_at` | integer | Unix timestamp when requested. |
| `completed_at` | integer? | When generation finished. |
| `expires_at` | integer? | When the download expires. |
| `format` | string | `json` or `zip`. |
| `file_size_bytes` | integer? | Export file size. |
| `checksum` | string? | SHA-256 checksum of the file. |

### Get a data export request

```
GET /api/v1/users/@me/data-export/{request_id}
```

Returns one request by ID. `request_id` must be an integer string; non-numeric values return `400 Invalid request ID`.

**Response** `200 OK` (`DSARRequestResponse`) — same shape as one entry in the list above.

**Errors**:

| Status | Condition |
|--------|-----------|
| `400` | Invalid request ID format. |
| `401` | Not authenticated. |
| `404` | Request not found (or not owned by the current user). |

### Cancel a data export request

```
DELETE /api/v1/users/@me/data-export/{request_id}
```

Cancels a pending, approved, or generating request. The generation worker's conditional status updates ensure a cancelled row isn't resurrected by an in-flight generation (`plexichat/src/api/routes/users/dsar.py:235`).

**Errors**:

| Status | Condition |
|--------|-----------|
| `400` | Request is not in `pending`, `approved`, or `generating` (`Only pending, approved or generating requests can be cancelled`). |
| `400` | Invalid request ID format. |
| `401` | Not authenticated. |
| `404` | Request not found. |

**Response** `200 OK`

```json
{
  "success": true,
  "message": "Data export request cancelled"
}
```

### Download a data export

```
GET /api/v1/users/@me/data-export/{request_id}/download
```

Streams the export file. This endpoint uses `StreamingResponse` and has no JSON `response_model` (`plexichat/src/api/routes/users/composer.py:238`).

**Preconditions**:

- The request status must be `ready` or `downloaded`. The manager deliberately allows `downloaded` so you can download again within the retention window (`plexichat/src/api/routes/users/dsar.py:296`).
- The `expires_at` timestamp must be in the future. Expired requests return `410 Download link has expired`.
- The storage path must exist. The server constructs an `ExportFormatGenerator` with the live DB handle so the `database` storage backend works (`plexichat/src/api/routes/users/dsar.py:346`). If the stream can't be opened, the server returns `500 Failed to read export from storage`.

**Response** `200 OK` — binary stream.

| Header | Description |
|--------|-------------|
| `Content-Disposition` | `attachment; filename="dsar_export_{id}.{format}"` |
| `Content-Length` | File size in bytes (string). |
| `Content-Type` | `application/zip` for `zip`, `application/json` for `json`. |
| `X-DSAR-Checksum` | SHA-256 checksum of the file. |
| `X-DSAR-Request-Id` | Request ID. |

Example:

```bash
curl -OJ http://localhost:8000/api/v1/users/@me/data-export/123/download \
  -H "Authorization: Bearer YOUR_SESSION_TOKEN"
```

**One-shot marker behavior**:

The server marks the export as `downloaded` only after all bytes have been sent (`plexichat/src/api/routes/users/dsar.py:382`). If the client disconnects mid-stream, the generator is closed with `GeneratorExit`, the `mark_export_downloaded` call is skipped, and the export stays re-downloadable. Failures to mark downloaded are logged at `WARNING` but don't fail the download.

**Errors**:

| Status | Condition |
|--------|-----------|
| `400` | Export is not in `ready` or `downloaded` (`Export is not available for download`). |
| `400` | Invalid request ID format. |
| `401` | Not authenticated. |
| `404` | Request not found or export file not found. |
| `404` | Export file path not available. |
| `410` | Download link has expired. |
| `500` | Failed to read export from storage or internal error. |

## User objects

### Private user object

Returned for `GET /users/@me`:

```json
{
  "id": "123456789012345678",
  "username": "johndoe",
  "email": "john@example.com",
  "avatar_url": "https://cdn.example.com/avatars/123.png",
  "created_at": 1704067200,
  "email_verified": true,
  "totp_enabled": false,
  "age_verified": false,
  "badges": ["early_supporter"]
}
```

### Public user object

Returned for `GET /users/{user_id}` and `GET /users/search`:

```json
{
  "id": "123456789012345678",
  "username": "johndoe",
  "avatar_url": "https://api.plexichat.com/avatars/users/123456789012345678",
  "created_at": 1704067200,
  "badges": []
}
```

## Related endpoints

- [User Settings](settings.md) — Cloud-synced preferences, including bulk updates
- [Authentication](authentication.md) — Login, registration, and 2FA
