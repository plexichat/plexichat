# Users API

Endpoints for user profile management.

## GET /users/@me

Get the current authenticated user's profile.

### Headers

```
Authorization: Bearer <token>
```

### Response (200 OK)

```json
{
  "id": "123456789012345678",
  "username": "johndoe",
  "email": "john@example.com",
  "avatar_url": "https://cdn.example.com/avatars/123.png",
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

## PATCH /users/@me

Update the current user's profile.

### Headers

```
Authorization: Bearer <token>
```

### Request Body

| Field | Type | Required | Constraints | Description |
|-------|------|----------|-------------|-------------|
| username | string | No | 3-32 characters | New username |
| email | string | No | Valid email | New email |
| password | string | No | Min 8 characters | New password |
| current_password | string | Conditional | - | Required if changing password |

### Example Request

```json
{
  "username": "newusername"
}
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

### Headers

```
Authorization: Bearer <token>
Content-Type: multipart/form-data
```

### Request Body

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| file | file | Yes | Image file (JPEG, PNG, GIF, WebP) |

### Response (200 OK)

```json
{
  "success": true,
  "avatar_url": "/api/v1/avatars/user/123456789012345678",
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

### Headers

```
Authorization: Bearer <token>
```

### Query Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| username | string | Yes | Username to search for (exact match, case-insensitive) |

### Example Request

```
GET /users/search?username=johndoe
```

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

Note: Private fields (email, email_verified, totp_enabled) are not included in public profiles.

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
  "age_verified": false
}
```

### Public User Object

Returned for other users (`/users/{user_id}`).

```json
{
  "id": "123456789012345678",
  "username": "johndoe",
  "avatar_url": "https://cdn.example.com/avatars/123.png",
  "created_at": 1704067200
}
```

---

## Related Endpoints

- [User Settings](settings.md) - Cloud-synced user preferences
- [User Features](features.md) - Badges, tiers, and feature flags
- [Authentication](authentication.md) - Login, registration, and 2FA
