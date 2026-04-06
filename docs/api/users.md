# Users API

Endpoints for user profile management.

**Base URL**: `https://api.plexichat.com`

## GET /users/@me

Get the current authenticated user's profile.

### Example Request

```bash
curl -X GET https://api.plexichat.com/users/@me \
  -H "Authorization: Bearer YOUR_SESSION_TOKEN"
```

### Response (200 OK)

```json
{
  "id": "123456789012345678",
  "username": "johndoe",
  "email": "john@example.com",
  "avatar_url": "https://api.plexichat.com/avatars/users/123456789012345678",
  "created_at": 1704067200,
  "email_verified": true,
  "totp_enabled": false,
  "age_verified": false
}
```

### Response Fields

| Field | Type | Description |
|-------|------|-------------|
| id | string | User's snowflake ID |
| username | string | Username |
| email | string | Email address (private) |
| avatar_url | string? | Avatar URL |
| created_at | int | Unix timestamp of account creation |
| email_verified | bool | Email verification status |
| totp_enabled | bool | 2FA enabled status |
| age_verified | bool | Age verification status |
| badges | array | Array of user badge identifiers |

## GET /users/@me/messaging-settings

Get current authenticated user's messaging preferences.

### Response (200 OK)

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

## PATCH /users/@me/messaging-settings

Update current authenticated user's messaging preferences.

### Request Body

All fields optional.

| Field | Type | Description |
|-------|------|-------------|
| read_receipts_enabled | bool | Send read receipts to others |
| typing_indicators_enabled | bool | Show your typing status |
| compact_messages_enabled | bool | Enable message grouping |
| allow_dms_from | string | "everyone", "friends", or "none" |
| auto_create_dms | bool | Automatically create conversations |

### Example Request

```json
{
  "read_receipts_enabled": false,
  "allow_dms_from": "friends"
}
```

### Response (200 OK)

Returns updated messaging settings object.

## PATCH /users/@me

Update the current user's profile.

### Request Body

| Field | Type | Required | Constraints | Description |
|-------|------|----------|-------------|-------------|
| username | string | No | 3-32 characters | New username |
| email | string | No | Valid email | New email |
| password | string | No | Min 8 characters | New password |
| current_password | string | Conditional | - | Required if changing password |

### Example Request

```bash
curl -X PATCH https://api.plexichat.com/users/@me \
  -H "Authorization: Bearer YOUR_SESSION_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "username": "newusername"
  }'
```

### Response (200 OK)

Returns the updated user object.

### Error Responses

| Status | Code | Description |
|--------|------|-------------|
| 400 | Invalid input | Validation failed |
| 400 | Weak password | Password doesn't meet requirements |
| 400 | Missing current_password | Required for password change |
| 409 | Already exists | Username or email taken |

## POST /users/@me/avatar

Upload a new avatar for the current user.

### Example Request

```bash
curl -X POST https://api.plexichat.com/users/@me/avatar \
  -H "Authorization: Bearer YOUR_SESSION_TOKEN" \
  -F "file=@/path/to/avatar.png"
```

### Response (200 OK)

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

### Error Responses

| Status | Code | Description |
|--------|------|-------------|
| 400 | Invalid file type | File must be an image |
| 400 | File too large | Exceeds size limit |

## GET /users/@me/notes

Get or create the personal notes channel for the current user.

Personal notes are a single-user conversation for storing private notes that sync across devices.

### Headers

```
Authorization: Bearer <token>
```

### Response (200 OK)

```json
{
  "id": "123456789012345678",
  "type": "notes",
  "name": "Personal Notes",
  "last_message_id": "234567890123456789",
  "last_message_at": 1704067200
}
```

## GET /users/@me/channels

Get all DM channels for the current user.

### Headers

```
Authorization: Bearer <token>
```

### Response (200 OK)

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

### Response Fields

| Field | Type | Description |
|-------|------|-------------|
| id | string | DM channel's snowflake ID |
| type | string | Always "dm" |
| recipient_id | string | Other user's ID |
| recipient | object | Recipient user info |
| last_message_id | string? | ID of last message |

## POST /users/@me/channels

Create or get a DM channel with a user.

### Headers

```
Authorization: Bearer <token>
```

### Request Body

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| recipient_id | string | Yes | Target user's snowflake ID |

### Example Request

```json
{
  "recipient_id": "234567890123456789"
}
```

### Response (200 OK)

```json
{
  "id": "123456789012345678",
  "type": "dm",
  "recipient_id": "234567890123456789",
  "recipient": {
    "id": "234567890123456789",
    "username": "janedoe"
  }
}
```

### Error Responses

| Status | Code | Description |
|--------|------|-------------|
| 400 | recipient_id required | Missing recipient_id |
| 400 | Invalid recipient ID | ID format invalid |
| 403 | Cannot message | User has blocked you |
| 404 | User not found | Recipient doesn't exist |

## GET /users/search

Search for a user by username.

### Query Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| username | string | Yes | Username to search for (exact match, case-insensitive) |

### Example Request

```bash
curl -X GET "https://api.plexichat.com/users/search?username=johndoe" \
  -H "Authorization: Bearer YOUR_SESSION_TOKEN"
```

### Response (200 OK)

```json
{
  "id": "123456789012345678",
  "username": "johndoe",
  "avatar_url": "https://api.plexichat.com/avatars/users/123456789012345678",
  "created_at": 1704067200
}
```

### Error Responses

| Status | Code | Description |
|--------|------|-------------|
| 400 | Username required | Missing username parameter |
| 404 | User not found | No user with that username |

## GET /users/{user_id}

Get public profile information for a user.

### Headers

```
Authorization: Bearer <token>
```

### Path Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| user_id | string | User's snowflake ID |

### Response (200 OK)

```json
{
  "id": "123456789012345678",
  "username": "johndoe",
  "avatar_url": "https://cdn.example.com/avatars/123.png",
  "created_at": 1704067200
}
```



### Error Responses

| Status | Code | Description |
|--------|------|-------------|
| 400 | Invalid user ID | ID format invalid |
| 404 | User not found | User doesn't exist |

---

## User Objects

### Full User Object (Private)

Returned for the authenticated user (`/users/@me`).

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

### Public User Object

Returned for other users (`/users/{user_id}`).

```json
{
  "id": "123456789012345678",
  "username": "johndoe",
  "avatar_url": "https://api.plexichat.com/avatars/users/123456789012345678",
  "created_at": 1704067200,
  "badges": []
}
```

---

## Related Endpoints

- [User Settings](settings.md) - Cloud-synced user preferences
- [User Features](features.md) - Badges, tiers, and feature flags
- [Authentication](authentication.md) - Login, registration, and 2FA
