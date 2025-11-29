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
  "totp_enabled": false
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
| avatar_url | string | No | Valid URL | New avatar URL |
| password | string | No | Min 8 characters | New password |
| current_password | string | Conditional | - | Required if changing password |

### Example Request

```json
{
  "username": "newusername",
  "avatar_url": "https://cdn.example.com/avatars/new.png"
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

### Response Fields

| Field | Type | Description |
|-------|------|-------------|
| id | string | User's snowflake ID |
| username | string | Username |
| avatar_url | string? | Avatar URL |
| created_at | int | Unix timestamp of account creation |

Note: Private fields (email, email_verified, totp_enabled) are not included in public profiles.

### Error Responses

| Status | Code | Description |
|--------|------|-------------|
| 400 | Invalid user ID | ID format invalid |
| 404 | User not found | User doesn't exist |

## User Object

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
  "totp_enabled": false
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
